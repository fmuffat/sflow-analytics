from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import names, oui, rdns
from ..db import Database, get_db

router = APIRouter(tags=["names"])


class AliasBody(BaseModel):
    kind: Literal["ip", "mac", "cidr"]
    key: str = Field(..., max_length=64)
    name: str = Field(..., min_length=1, max_length=64)
    notes: str = Field("", max_length=500)


@router.get("/aliases", summary="Manual aliases (IP, MAC, subnet)")
def list_aliases(db: Database = Depends(get_db)):
    return {"items": names.list_aliases(db)}


@router.put("/aliases", summary="Create or update an alias")
def put_alias(body: AliasBody, db: Database = Depends(get_db)):
    try:
        return names.set_alias(db, body.kind, body.key, body.name, body.notes)
    except names.AliasError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/aliases", summary="Delete an alias")
def delete_alias(kind: Literal["ip", "mac", "cidr"], key: str = Query(..., max_length=64), db: Database = Depends(get_db)):
    try:
        names.delete_alias(db, kind, key)
    except names.AliasError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"status": "deleted"}


@router.post("/aliases/import", summary="Import aliases from CSV (kind,key,name[,notes])")
def import_aliases(csv_text: str = Body(..., media_type="text/csv", max_length=2_000_000), db: Database = Depends(get_db)):
    return names.import_csv(db, csv_text)


@router.get("/aliases/export", summary="Export aliases as CSV")
def export_aliases(db: Database = Depends(get_db)):
    return Response(names.export_csv(db), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="aliases.csv"'})


@router.get("/hosts", summary="Search hosts by name (aliases, controller clients, DNS)")
def search_hosts(q: str = Query(..., min_length=2, max_length=64), db: Database = Depends(get_db)):
    return {"items": names.search(db, q)}


@router.get("/hosts/{ip}", summary="Name of one IP and where it comes from")
def host(ip: str, db: Database = Depends(get_db)):
    try:
        key = names.normalize_key("ip", ip)
    except names.AliasError as exc:
        raise HTTPException(422, str(exc)) from exc
    d = names.details_for(db, [key]).get(key, {})
    return {"ip": key, "name": d.get("name"), "source": d.get("source"), "mac": d.get("mac"), "vendor": d.get("vendor")}


@router.get("/mac/{mac}", summary="Vendor of a MAC address (IEEE registry, bundled)")
def mac_vendor(mac: str):
    try:
        key = names.normalize_key("mac", mac)
    except names.AliasError as exc:
        raise HTTPException(422, str(exc)) from exc
    r = oui.lookup(key)
    return {"mac": key, "vendor": r["vendor"] if r else None, "organization": r["organization"] if r else None}


class DnsSettings(BaseModel):
    enabled: bool | None = None
    servers: list[str] | None = Field(None, max_length=4)
    max_per_run: int | None = Field(None, ge=10, le=5000)


@router.get("/admin/dns", summary="Reverse DNS settings and cache statistics")
def get_dns(db: Database = Depends(get_db)):
    return {**rdns.get_config(), "cache": rdns.stats(db)}


@router.put("/admin/dns", summary="Update reverse DNS settings")
def put_dns(body: DnsSettings, db: Database = Depends(get_db)):
    try:
        rdns.save_config(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return get_dns(db)


@router.post("/admin/dns/run", summary="Resolve pending IPs now")
def run_dns(db: Database = Depends(get_db)):
    return rdns.run(db)
