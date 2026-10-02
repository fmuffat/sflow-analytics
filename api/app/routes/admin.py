import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from .. import store
from ..config import get_settings
from ..db import Database, get_db
from .health import _collector_status, system_status

router = APIRouter(prefix="/admin", tags=["administration"])

# Settings shown in Administration; secrets are never returned.
PUBLIC_SETTINGS = ("app_name", "app_version", "app_timezone", "retention_days", "clickhouse_host",
                   "clickhouse_database", "auth_enabled", "admin_username", "session_idle_minutes",
                   "session_max_hours", "cookie_secure")


@router.get("/config", summary="Effective configuration (secrets excluded)")
def config():
    s = get_settings()
    return {k: getattr(s, k) for k in PUBLIC_SETTINGS}


@router.get("/jobs", summary="Background jobs and their last run")
def jobs():
    return {"items": store.query("SELECT * FROM jobs ORDER BY name")}


@router.get("/diagnostics", summary="Diagnostics bundle (JSON download)")
def diagnostics(db: Database = Depends(get_db)):
    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config(),
        "system": system_status(db),
        "collector": _collector_status(),
        "jobs": jobs()["items"],
        "users": store.query("SELECT username, role, created_at, last_login_at FROM users"),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(json.dumps(bundle, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="sflow-diagnostics-{stamp}.json"'})
