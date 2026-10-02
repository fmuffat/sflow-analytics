from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import inventory
from ..config import get_settings
from ..db import Database, get_db

router = APIRouter(tags=["inventory"])

INACTIVE_AFTER_SECONDS = 300


def _live_exporters() -> dict[str, dict]:
    """Live state from the collector; empty when it is unreachable."""
    try:
        r = httpx.get(get_settings().collector_status_url + "/v1/exporters", timeout=2)
        r.raise_for_status()
        return {e["id"]: e for e in r.json()}
    except (httpx.HTTPError, ValueError):
        return {}


def _with_status(e: dict, live: dict[str, dict]) -> dict:
    l = live.get(e["id"])
    if l:
        e["status"] = l.get("status")
        e["samples_per_second"] = l.get("samples_per_second")
        e["lost_datagrams"] = l.get("lost_datagrams")
        e["errors"] = l.get("errors")
        e["datagrams"] = l.get("datagrams")
        e["last_seen"] = l.get("last_seen") or e["last_seen"]
        e["status_source"] = "collector"
    else:
        last = e["last_seen"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        e["status"] = "active" if age <= INACTIVE_AFTER_SECONDS else "inactive"
        e["status_source"] = "database"
    return e


@router.get("/exporters", summary="Discovered exporters (switches)")
def list_exporters(db: Database = Depends(get_db)):
    live = _live_exporters()
    return {"items": [_with_status(e, live) for e in inventory.exporters(db)]}


@router.get("/exporters/{exporter_id:path}", summary="One exporter with its interfaces")
def get_exporter(exporter_id: str, db: Database = Depends(get_db)):
    e = inventory.exporter(db, exporter_id)
    if not e:
        raise HTTPException(404, "exporter not found")
    e = _with_status(e, _live_exporters())
    e["interfaces"] = inventory.interfaces(db, exporter_id)
    return e


class ExporterPatch(BaseModel):
    display_name: str | None = Field(None, max_length=64, description="Friendly name; empty string clears it")
    notes: str | None = Field(None, max_length=2000)


@router.patch("/exporters/{exporter_id:path}", summary="Rename an exporter / edit notes")
def patch_exporter(exporter_id: str, body: ExporterPatch, db: Database = Depends(get_db)):
    e = inventory.exporter(db, exporter_id)
    if not e:
        raise HTTPException(404, "exporter not found")
    name = (body.display_name if body.display_name is not None else e["display_name"] or "").strip()
    notes = body.notes if body.notes is not None else (e["notes"] or "")
    inventory.save_exporter_settings(db, exporter_id, name, notes)
    return get_exporter(exporter_id, db)


@router.get("/interfaces", summary="Interfaces seen per exporter")
def list_interfaces(exporter: str | None = None, db: Database = Depends(get_db)):
    return {"items": inventory.interfaces(db, exporter)}


def _split_interface_id(interface_id: str) -> tuple[str, int]:
    exporter_id, _, idx = interface_id.rpartition("/")
    if not exporter_id or not idx.isdigit():
        raise HTTPException(404, "interface id must be <exporter_id>/<ifindex>")
    return exporter_id, int(idx)


@router.get("/interfaces/{interface_id:path}", summary="One interface (<exporter_id>/<ifindex>)")
def get_interface(interface_id: str, db: Database = Depends(get_db)):
    exporter_id, idx = _split_interface_id(interface_id)
    i = inventory.interface(db, exporter_id, idx)
    if not i:
        raise HTTPException(404, "interface not found")
    return i


class InterfacePatch(BaseModel):
    name: str | None = Field(None, max_length=64, description="Friendly name, e.g. 1/1/48; empty string clears it")
    description: str | None = Field(None, max_length=500)


@router.patch("/interfaces/{interface_id:path}", summary="Name an interface (manual ifIndex mapping)")
def patch_interface(interface_id: str, body: InterfacePatch, db: Database = Depends(get_db)):
    exporter_id, idx = _split_interface_id(interface_id)
    i = inventory.interface(db, exporter_id, idx)
    if not i:
        raise HTTPException(404, "interface not found")
    name = (body.name if body.name is not None else i["name"] or "").strip()
    desc = body.description if body.description is not None else (i["description"] or "")
    inventory.save_interface_settings(db, exporter_id, idx, name, desc)
    return inventory.interface(db, exporter_id, idx)
