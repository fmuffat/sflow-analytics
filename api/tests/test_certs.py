"""HTTPS certificate management (no ClickHouse needed)."""

import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from app import certs

UI = {"X-Requested-With": "sflow"}
NOW = datetime.now(timezone.utc)


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


@pytest.fixture(scope="module")
def ca():
    key = _key()
    cert = (x509.CertificateBuilder().subject_name(_name("Test CA")).issuer_name(_name("Test CA"))
            .public_key(key.public_key()).serial_number(1).not_valid_before(NOW - timedelta(days=1))
            .not_valid_after(NOW + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return key, cert


def _issue(ca, public_key, cn="sflow.example.com", days=365, start=-1):
    ca_key, ca_cert = ca
    return (x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(ca_cert.subject)
            .public_key(public_key).serial_number(x509.random_serial_number())
            .not_valid_before(NOW + timedelta(days=start)).not_valid_after(NOW + timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
            .sign(ca_key, hashes.SHA256()))


def pem(obj):
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM).decode()
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


@pytest.fixture()
def certdir(tmp_path, monkeypatch):
    monkeypatch.setattr(certs, "CERT_DIR", tmp_path)
    monkeypatch.setenv("TLS_COMMON_NAME", "sflow-analytics")
    monkeypatch.setenv("TLS_EXTRA_SAN", "IP:192.0.2.10")
    return tmp_path


def test_self_signed_and_info(certdir):
    r = certs.self_signed()
    assert r["installed"] and r["self_signed"] and r["subject"] == "sflow-analytics"
    assert set(r["names"]) == {"DNS:sflow-analytics", "DNS:localhost", "IP:192.0.2.10"}
    assert r["days_left"] > 800 and not r["expired"]
    assert (certdir / "server.key").stat().st_mode & 0o777 == 0o600
    assert any("self-signed" in w for w in r["warnings"])


def test_import_pem_with_chain(certdir, ca):
    key = _key()
    leaf = _issue(ca, key.public_key())
    r = certs.import_pem(pem(leaf), pem(key), pem(ca[1]))
    assert r["subject"] == "sflow.example.com" and not r["self_signed"] and r["issuer"] == "Test CA"
    assert r["chain_length"] == 2 and r["warnings"] == []
    # leaf first in the written file
    first = x509.load_pem_x509_certificates((certdir / "server.crt").read_bytes())[0]
    assert first.subject == leaf.subject
    # previous files kept
    certs.import_pem(pem(leaf), pem(key))
    assert (certdir / "server.crt.bak").exists() and (certdir / "server.key.bak").exists()


def test_rejections(certdir, ca):
    key, other = _key(), _key()
    leaf = _issue(ca, key.public_key())
    with pytest.raises(certs.CertError, match="does not match"):
        certs.import_pem(pem(leaf), pem(other))
    with pytest.raises(certs.CertError, match="expired"):
        certs.import_pem(pem(_issue(ca, key.public_key(), days=-1, start=-10)), pem(key))
    with pytest.raises(certs.CertError, match="unreadable certificate"):
        certs.import_pem("not a certificate", pem(key))
    with pytest.raises(certs.CertError, match="private key is required"):
        certs.import_pem(pem(leaf), None)
    assert not (certdir / "server.crt").exists()  # nothing written on error


def test_pfx(certdir, ca):
    key = _key()
    leaf = _issue(ca, key.public_key(), cn="pfx.example.com")
    data = pkcs12.serialize_key_and_certificates(b"sflow", key, leaf, [ca[1]],
                                                 serialization.BestAvailableEncryption(b"secret"))
    with pytest.raises(certs.CertError, match="wrong password"):
        certs.import_pfx(data, "bad")
    r = certs.import_pfx(data, "secret")
    assert r["subject"] == "pfx.example.com" and r["chain_length"] == 2


def test_csr_then_signed_certificate(certdir, ca):
    out = certs.create_csr(["sflow.corp.example", "IP:192.0.2.20"], "Example", "FR")
    csr = x509.load_pem_x509_csr(out["pem"].encode())
    assert csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "sflow.corp.example"
    assert certs.info()["pending_csr"]["subject"] == "sflow.corp.example"
    signed = _issue(ca, csr.public_key(), cn="sflow.corp.example")
    r = certs.import_pem(pem(signed), None, pem(ca[1]))  # key kept on the appliance
    assert r["subject"] == "sflow.corp.example" and r["pending_csr"] is None
    with pytest.raises(certs.CertError):
        certs.create_csr(["bad name!"])
    with pytest.raises(certs.CertError, match="country"):
        certs.create_csr(["a.example"], country="FRA")


def test_api_never_returns_the_key(certdir, ca):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/v1/admin/certificate/self-signed", headers=UI, json={})
        assert r.status_code == 200, r.text
        got = c.get("/api/v1/admin/certificate").json()
        assert got["installed"] and "PRIVATE KEY" not in str(got) and got["default_names"][0] == "sflow-analytics"
        key = _key()
        leaf = _issue(ca, key.public_key())
        r = c.post("/api/v1/admin/certificate/pfx", headers=UI, json={
            "pfx_base64": base64.b64encode(pkcs12.serialize_key_and_certificates(
                b"x", key, leaf, None, serialization.NoEncryption())).decode(), "password": ""})
        assert r.status_code == 200 and "PRIVATE KEY" not in r.text
        assert c.post("/api/v1/admin/certificate/pem", headers=UI,
                      json={"certificate": "junk", "private_key": "junk"}).status_code == 422
        assert c.post("/api/v1/admin/certificate/pfx", headers=UI,
                      json={"pfx_base64": "%%%", "password": ""}).status_code == 422
