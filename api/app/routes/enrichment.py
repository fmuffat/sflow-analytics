from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import Database, get_db
from ..enrichment import sync
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
