import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..db import Database, get_db

router = APIRouter(tags=["health"])          # protected
public_router = APIRouter(tags=["health"])   # no authentication


def _collector_status() -> dict | None:
    try:
        r = httpx.get(get_settings().collector_status_url + "/v1/status", timeout=3)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def _db_ok(db: Database) -> bool:
    try:
        return db.ping()
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


@public_router.get("/health", summary="API liveness and database reachability")
def health(db: Database = Depends(get_db)):
    ok = _db_ok(db)
    return JSONResponse({"status": "ok" if ok else "degraded", "clickhouse": ok}, status_code=200 if ok else 503)


@router.get("/collector/status", summary="Collector counters (proxied from the collector)")
def collector_status():
    st = _collector_status()
    if st is None:
        return JSONResponse({"status": "unreachable"}, status_code=502)
    return st


@router.get("/system/status", summary="Application, database and storage status")
def system_status(db: Database = Depends(get_db)):
    s = get_settings()
    st = _collector_status()
    return {
        "app_name": s.app_name,
        "version": s.app_version,
        "timezone": s.app_timezone,
        "retention_days": s.retention_days,
        "services": {
            "api": "running",
            "clickhouse": "running" if _db_ok(db) else "unreachable",
            "collector": st.get("status", "unknown") if st else "unreachable",
        },
        "collector_listen_address": st.get("listen_address") if st else None,
        "storage": st.get("storage") if st else None,
    }
