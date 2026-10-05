from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import updates

router = APIRouter(prefix="/admin/updates", tags=["administration"])


@router.get("", summary="Installed and latest published version")
def get_updates():
    return updates.status()


@router.post("/check", summary="Check GitHub for a new version now")
def check_now():
    return updates.check(force=True)


class UpdatesBody(BaseModel):
    enabled: bool


@router.put("", summary="Enable or disable the daily check")
def put_updates(body: UpdatesBody):
    return updates.set_enabled(body.enabled)


@router.get("/progress", summary="Progress of the update in progress (or of the last one)")
def get_progress():
    return {"progress": updates.progress(), "running": updates.running()}


class ApplyBody(BaseModel):
    version: str


@router.post("/apply", summary="Install the latest release (backup first; the interface restarts)")
def apply(body: ApplyBody):
    try:
        return updates.apply(body.version)
    except updates.UpdateError as exc:
        raise HTTPException(409, str(exc)) from exc
