import base64
import binascii

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import certs

router = APIRouter(prefix="/admin/certificate", tags=["administration"])


def _do(fn, *args):
    try:
        return fn(*args)
    except certs.CertError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("", summary="Installed HTTPS certificate (and pending CSR)")
def get_certificate():
    return {**certs.info(), "default_names": certs.default_names()}


class PemBody(BaseModel):
    certificate: str = Field(..., max_length=100_000, description="PEM, leaf first (chain may follow)")
    private_key: str | None = Field(None, max_length=100_000, description="PEM; omit after a CSR made here")
    chain: str | None = Field(None, max_length=100_000, description="intermediate certificates, PEM")
    key_password: str | None = Field(None, max_length=200)


@router.post("/pem", summary="Install a PEM certificate and private key")
def import_pem(body: PemBody):
    return _do(certs.import_pem, body.certificate, body.private_key, body.chain, body.key_password)


class PfxBody(BaseModel):
    pfx_base64: str = Field(..., max_length=300_000)
    password: str = Field("", max_length=200)


@router.post("/pfx", summary="Install a PFX / PKCS#12 file (certificate + key + chain)")
def import_pfx(body: PfxBody):
    try:
        data = base64.b64decode(body.pfx_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "invalid file encoding") from exc
    return _do(certs.import_pfx, data, body.password)


class CsrBody(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=20, description="host names / IPs, the first one is the CN")
    organization: str = Field("", max_length=64)
    country: str = Field("", max_length=2)


@router.post("/csr", summary="New private key (kept here) and certificate signing request")
def create_csr(body: CsrBody):
    return _do(certs.create_csr, body.names, body.organization, body.country)


class SelfSignedBody(BaseModel):
    names: list[str] | None = Field(None, max_length=20)


@router.post("/self-signed", summary="Back to a new self-signed certificate")
def self_signed(body: SelfSignedBody):
    return _do(certs.self_signed, body.names)
