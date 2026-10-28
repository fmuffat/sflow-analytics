from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import Database, get_db
from ..enrichment import snmp, sync
from ..enrichment.ruckusone import REGIONS

router = APIRouter(prefix="/admin/enrichment", tags=["administration"])


class R1Settings(BaseModel):
    region: Literal["na", "eu", "asia"] | None = None
    tenant_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9-]*$")
    client_id: str | None = Field(None, max_length=128)
    client_secret: str | None = Field(None, max_length=256, description="write-only; empty keeps the stored secret")


class SZSettings(BaseModel):
    host: str | None = Field(None, max_length=253, pattern=r"^[A-Za-z0-9.\-:\[\]]*$")
    port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, max_length=128)
    password: str | None = Field(None, max_length=256, description="write-only; empty keeps the stored password")
    verify_tls: bool | None = None


class EnrichmentSettings(BaseModel):
    source: Literal["none", "ruckusone", "smartzone"] | None = None
    interval_minutes: int | None = Field(None, ge=5, le=1440)
    ruckusone: R1Settings | None = None
    smartzone: SZSettings | None = None


@router.get("", summary="Enrichment settings (secrets never returned) and last sync")
def get_settings_():
    return {
        **sync.get_config(),
        "regions": {k: v[0] for k, v in REGIONS.items()},
        "smartzone_available": True,
        "last_sync": sync.last_sync(),
    }


@router.put("", summary="Update enrichment settings")
def put_settings(body: EnrichmentSettings):
    try:
        sync.save_config(body.model_dump(exclude_none=True))
    except sync.SyncError as exc:
        raise HTTPException(422, str(exc)) from exc
    return get_settings_()


@router.post("/test", summary="Test the controller connection (no data written)")
def test():
    try:
        return sync.test_connection()
    except sync.SyncError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sync", summary="Synchronize the controller inventory now")
def sync_now(db: Database = Depends(get_db)):
    return sync.run_sync(db)


class SnmpV3(BaseModel):
    username: str | None = Field(None, max_length=64)
    auth_protocol: Literal["SHA", "SHA-256", "SHA-512", "MD5"] | None = None
    auth_key: str | None = Field(None, max_length=128, description="write-only; empty keeps the stored key")
    priv_protocol: Literal["AES", "AES-256", "DES", "none"] | None = None
    priv_key: str | None = Field(None, max_length=128, description="write-only; empty keeps the stored key")


class SnmpSettings(BaseModel):
    enabled: bool | None = None
    version: Literal["v2c", "v3"] | None = None
    port: int | None = Field(None, ge=1, le=65535)
    timeout: float | None = Field(None, ge=0.5, le=10)
    interval_minutes: int | None = Field(None, ge=5, le=1440)
    community: str | None = Field(None, max_length=128, description="write-only; empty keeps the stored community")
    v3: SnmpV3 | None = None


@router.get("/snmp", summary="SNMP polling settings (secrets never returned), targets and last poll")
def get_snmp(db: Database = Depends(get_db)):
    return {**snmp.get_config(), "targets": snmp.targets(db), "last_poll": snmp.last_poll()}


@router.put("/snmp", summary="Update SNMP polling settings")
def put_snmp(body: SnmpSettings, db: Database = Depends(get_db)):
    try:
        snmp.save_config(body.model_dump(exclude_none=True))
    except snmp.SnmpError as exc:
        raise HTTPException(422, str(exc)) from exc
    return get_snmp(db)


class SnmpTest(BaseModel):
    ip: str | None = Field(None, max_length=45, pattern=r"^[0-9A-Fa-f.:]*$")


@router.post("/snmp/test", summary="Test SNMP on one switch (default: the first target); nothing written")
def test_snmp(body: SnmpTest, db: Database = Depends(get_db)):
    try:
        return snmp.test(db, body.ip or None)
    except snmp.SnmpError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/snmp/poll", summary="Poll the switches now")
def poll_snmp(db: Database = Depends(get_db)):
    return snmp.run_poll(db)
