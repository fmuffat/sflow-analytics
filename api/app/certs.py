"""HTTPS certificate of the appliance (nginx), managed from Administration.

The certificate and its private key live in the `nginx-certs` volume, mounted in the API at
CERT_DIR. nginx checks the files every few seconds and reloads itself when they change
(nginx/20-cert-watch.sh), so a new certificate applies without downtime.

- info(): what is installed (subject, issuer, names, validity, self-signed or not)
- import_pem() / import_pfx(): validated before being written (key matches the certificate,
  dates, names), previous files kept as *.bak
- create_csr(): new private key kept on the appliance (pending.key) and a CSR to have signed
  by a company CA; the signed certificate is then imported without key
- self_signed(): back to a self-signed certificate for the host names / IPs of the appliance
The private key is never returned by the API.
"""

from __future__ import annotations

import ipaddress
import re
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERT_DIR = Path(os.environ.get("CERT_DIR", "/certs"))
_DNS = re.compile(r"^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
CRT, KEY, PENDING_KEY, CSR = "server.crt", "server.key", "pending.key", "pending.csr"
MAX_BYTES = 200_000


class CertError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(name: str, data: bytes, mode: int = 0o644) -> None:
    """Atomic write in CERT_DIR (rename), keeping the previous version as name.bak."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    path = CERT_DIR / name
    tmp = CERT_DIR / f".{name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    if path.exists():
        os.replace(path, CERT_DIR / f"{name}.bak")
    os.replace(tmp, path)


# --- reading -------------------------------------------------------------------------

def _names(cert: x509.Certificate) -> list[str]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return [f"DNS:{n}" for n in san.get_values_for_type(x509.DNSName)] + \
           [f"IP:{a}" for a in san.get_values_for_type(x509.IPAddress)]


def _cn(name: x509.Name) -> str:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else name.rfc4514_string()


def describe(cert: x509.Certificate) -> dict[str, Any]:
    nb, na = cert.not_valid_before_utc, cert.not_valid_after_utc
    pub = cert.public_key()
    key = f"RSA {pub.key_size}" if isinstance(pub, rsa.RSAPublicKey) else \
          f"EC {pub.curve.name}" if isinstance(pub, ec.EllipticCurvePublicKey) else type(pub).__name__
    return {
        "subject": _cn(cert.subject), "issuer": _cn(cert.issuer),
        "self_signed": cert.issuer == cert.subject,
        "names": _names(cert),
        "not_before": nb.isoformat(), "not_after": na.isoformat(),
        "days_left": (na - _now()).days, "expired": na < _now(),
        "key": key, "serial": format(cert.serial_number, "x"),
        "sha256": cert.fingerprint(hashes.SHA256()).hex(":").upper(),
    }


def info() -> dict[str, Any]:
    path = CERT_DIR / CRT
    out: dict[str, Any] = {"installed": False, "pending_csr": None}
    if path.exists():
        chain = x509.load_pem_x509_certificates(path.read_bytes())
        out.update(describe(chain[0]), installed=True, chain_length=len(chain))
    if (CERT_DIR / CSR).exists() and (CERT_DIR / PENDING_KEY).exists():
        csr = x509.load_pem_x509_csr((CERT_DIR / CSR).read_bytes())
        out["pending_csr"] = {"subject": _cn(csr.subject), "pem": (CERT_DIR / CSR).read_text()}
    return out


# --- validation and installation ------------------------------------------------------------

def _load_key(pem: bytes, password: bytes | None = None):
    try:
        return serialization.load_pem_private_key(pem, password=password)
    except (ValueError, TypeError) as exc:
        raise CertError("unreadable private key (PEM expected; encrypted keys need their password)") from exc


def _same_key(cert: x509.Certificate, key) -> bool:
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return cert.public_key().public_bytes(*fmt) == key.public_key().public_bytes(*fmt)


def _install(chain: list[x509.Certificate], key, warnings: list[str]) -> dict[str, Any]:
    leaf = chain[0]
    if not _same_key(leaf, key):
        raise CertError("the private key does not match the certificate")
    if leaf.not_valid_after_utc < _now():
        raise CertError(f"the certificate expired on {leaf.not_valid_after_utc:%Y-%m-%d}")
    if leaf.not_valid_before_utc > _now() + timedelta(minutes=5):
        raise CertError(f"the certificate is not valid before {leaf.not_valid_before_utc:%Y-%m-%d}")
    # Put the leaf first, then the other certificates (intermediates), without duplicates.
    seen, ordered = set(), []
    for c in chain:
        fp = c.fingerprint(hashes.SHA256())
        if fp not in seen:
            seen.add(fp)
            ordered.append(c)
    pem = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in ordered)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    _write(KEY, key_pem, 0o600)
    _write(CRT, pem, 0o644)
    for n in (PENDING_KEY, CSR):
        (CERT_DIR / n).unlink(missing_ok=True)
    if leaf.issuer == leaf.subject:
        warnings.append("self-signed certificate: browsers will still show a warning")
    elif len(ordered) == 1:
        warnings.append("no intermediate certificate: add the CA chain if browsers do not trust it")
    if (leaf.not_valid_after_utc - _now()).days < 30:
        warnings.append(f"expires in {(leaf.not_valid_after_utc - _now()).days} days")
    return {**info(), "warnings": warnings}


def import_pem(cert_pem: str, key_pem: str | None, chain_pem: str | None = None,
               key_password: str | None = None) -> dict[str, Any]:
    if len(cert_pem) + len(key_pem or "") + len(chain_pem or "") > MAX_BYTES:
        raise CertError("files too large")
    try:
        chain = x509.load_pem_x509_certificates((cert_pem + "\n" + (chain_pem or "")).encode())
    except ValueError as exc:
        raise CertError("unreadable certificate (PEM expected: -----BEGIN CERTIFICATE-----)") from exc
    warnings: list[str] = []
    if key_pem and key_pem.strip():
        key = _load_key(key_pem.encode(), key_password.encode() if key_password else None)
    elif (CERT_DIR / PENDING_KEY).exists():
        key = _load_key((CERT_DIR / PENDING_KEY).read_bytes())  # signed answer to our CSR
    else:
        raise CertError("the private key is required (or generate a CSR first)")
    return _install(chain, key, warnings)


def import_pfx(data: bytes, password: str) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise CertError("file too large")
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(data, password.encode() if password else None)
    except ValueError as exc:
        raise CertError("unreadable PFX/P12 file or wrong password") from exc
    if not key or not cert:
        raise CertError("the PFX/P12 file must contain the certificate and its private key")
    return _install([cert, *(extra or [])], key, [])


# --- CSR and self-signed ---------------------------------------------------------------------

def _san(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n.removeprefix("IP:"))))
        except ValueError:
            dns = n.removeprefix("DNS:")
            if len(dns) > 253 or not _DNS.match(dns):
                raise CertError(f"invalid host name: {n}") from None
            entries.append(x509.DNSName(dns))
    if not entries:
        raise CertError("at least one host name or IP address is required")
    return x509.SubjectAlternativeName(entries)


def _subject(cn: str, org: str = "", country: str = "") -> x509.Name:
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn[:64])]
    if org:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org[:64]))
    if country:
        if len(country) != 2:
            raise CertError("country: 2-letter code, e.g. FR")
        attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country.upper()))
    return x509.Name(attrs)


def create_csr(names: list[str], org: str = "", country: str = "") -> dict[str, Any]:
    san = _san(names)
    cn = names[0].strip().removeprefix("DNS:").removeprefix("IP:")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(_subject(cn, org, country))
           .add_extension(san, critical=False)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .sign(key, hashes.SHA256()))
    _write(PENDING_KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()), 0o600)
    pem = csr.public_bytes(serialization.Encoding.PEM)
    _write(CSR, pem)
    return {"subject": cn, "names": names, "pem": pem.decode()}


def default_names() -> list[str]:
    """Names of the self-signed certificate: TLS_COMMON_NAME, localhost and TLS_EXTRA_SAN."""
    cn = os.environ.get("TLS_COMMON_NAME", "sflow-analytics")
    extra = [x.strip() for x in os.environ.get("TLS_EXTRA_SAN", "").split(",") if x.strip()]
    return [cn, "localhost", *extra]


def self_signed(names: list[str] | None = None, days: int = 825) -> dict[str, Any]:
    names = names or default_names()
    san = _san(names)
    cn = names[0].removeprefix("DNS:").removeprefix("IP:")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = _subject(cn)
    now = _now()
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days))
            .add_extension(san, critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    return _install([cert], key, [])
