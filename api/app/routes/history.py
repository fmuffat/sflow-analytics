from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import history
from ..db import Database, get_db
from .traffic import limit_param

router = APIRouter(prefix="/history", tags=["history"])

Period = Literal["day", "week", "month", "year"]
Compare = Literal["previous", "week", "year"]


@router.get("/compare", summary="Interface traffic: this period vs the previous one (or a year before)")
def compare(
    period: Period = Query("day"),
    at: date | None = Query(None, description="A local date inside the period (default: today)"),
    tz: str = Query("UTC", description="IANA time zone of the viewer, e.g. Europe/Paris"),
    compare: Compare = Query("previous", description="previous period, same weekday a week before (day), or a year before"),
    interface: list[str] = Query([], description="Interface id(s) exporter_ip/agent_ip/sub_id/ifindex"),
    if_group: str | None = Query(None, max_length=48),
    db: Database = Depends(get_db),
):
    """Aligned buckets (hours for day and week, days for month, months for year), from the
    long-term hourly history. Several interfaces are summed (peaks: upper bound)."""
    ids = [i for v in interface for i in v.split(",") if i.strip()]
    try:
        return history.compare(db, period, at, tz, compare, ids, if_group)
    except history.HistoryError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/interfaces", summary="Every interface: this period vs the reference period")
def interfaces(
    period: Period = Query("month"),
    at: date | None = Query(None),
    tz: str = Query("UTC"),
    compare: Compare = Query("previous"),
    if_group: str | None = Query(None, max_length=48),
    exporter: str | None = Query(None, max_length=200),
    limit: int = Depends(limit_param),
    db: Database = Depends(get_db),
):
    try:
        return history.interfaces_table(db, period, at, tz, compare, if_group, exporter, limit)
    except history.HistoryError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/status", summary="Long-term history coverage")
def status(db: Database = Depends(get_db)):
    return history.coverage(db)
