from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import require_admin, require_ready_user
from ..db import Database, get_db
from ..reports import service
from ..reports.data import SECTIONS

router = APIRouter(tags=["reports"])


def _err(exc: Exception) -> HTTPException:
    return HTTPException(404 if str(exc).startswith("no ") else 422, str(exc))


@router.get("/reports", summary="Generated reports (last 90 days)")
def list_reports(limit: int = Query(500, ge=1, le=2000), user: dict = Depends(require_ready_user)):
    admin = user["role"] == "admin"
    return {"items": [service.public_report(r, admin) for r in service.list_reports(limit)],
            "keep_days": service.KEEP_DAYS, "timezone": service.tz_name()}


@router.get("/reports/{report_id}/{fmt}", summary="Download a generated report (pdf or xlsx)")
def download(report_id: int, fmt: Literal["pdf", "xlsx"]):
    try:
        path, name = service.report_file(report_id, fmt)
    except service.ReportError as exc:
        raise _err(exc) from exc
    return FileResponse(path, media_type=service.MIME[fmt], filename=name)


@router.delete("/reports/{report_id}", summary="Delete a generated report")
def delete_report(report_id: int, _: dict = Depends(require_admin)):
    try:
        service.delete_report(report_id)
    except service.ReportError as exc:
        raise _err(exc) from exc
    return {"deleted": report_id}


# --- definitions (administrators) ---

class DefinitionBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    enabled: bool = True
    period: Literal["weekly", "monthly"]
    options: dict[str, Any] = {}
    delivery: dict[str, Any] = {}


@router.get("/report-definitions", summary="Report definitions")
def definitions(_: dict = Depends(require_admin)):
    return {"items": service.list_definitions(), "sections": list(SECTIONS), "defaults": service.OPTION_DEFAULTS,
            "timezone": service.tz_name(), "send_hour": service.SEND_HOUR}


@router.post("/report-definitions", summary="Create a report definition")
def create_definition(body: DefinitionBody, _: dict = Depends(require_admin)):
    try:
        return service.save_definition(body.model_dump())
    except service.ReportError as exc:
        raise _err(exc) from exc


@router.put("/report-definitions/{def_id}", summary="Update a report definition")
def update_definition(def_id: int, body: DefinitionBody, _: dict = Depends(require_admin)):
    try:
        return service.save_definition(body.model_dump(), def_id)
    except service.ReportError as exc:
        raise _err(exc) from exc


@router.delete("/report-definitions/{def_id}", summary="Delete a report definition (generated reports are kept)")
def delete_definition(def_id: int, _: dict = Depends(require_admin)):
    try:
        service.delete_definition(def_id)
    except service.ReportError as exc:
        raise _err(exc) from exc
    return {"deleted": def_id}


class RunBody(BaseModel):
    period: Literal["weekly", "monthly", "custom"] | None = Field(None, description="default: the definition's period")
    start: date | None = Field(None, description="custom period: first day (included)")
    end: date | None = Field(None, description="custom period: last day (included)")
    email: bool = False


@router.post("/report-definitions/{def_id}/run", status_code=202,
             summary="Report now: last complete period or a custom one (generated in the background)")
def run(def_id: int, body: RunBody, tasks: BackgroundTasks, user: dict = Depends(require_admin),
        db: Database = Depends(get_db)):
    try:
        d = service.get_definition(def_id)
        start = end = None
        if body.period == "custom":
            if not (body.start and body.end):
                raise service.ReportError("a custom period needs a start and an end date")
            start, end = service.custom_bounds(body.start, body.end)
        rep = service.queue(d, "manual", user["username"], body.period, start, end, body.email)
    except service.ReportError as exc:
        raise _err(exc) from exc
    tasks.add_task(service.generate, db, rep["id"])
    return service.public_report(rep, True)
