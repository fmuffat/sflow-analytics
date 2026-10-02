from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import utilization
from ..config import get_settings
from ..db import Database, get_db
from ..filters import TrafficFilter, traffic_filter
from .traffic import limit_param

router = APIRouter(prefix="/utilization", tags=["utilization"])


@router.get("/interfaces", summary="Interface utilization (avg / p95 / peak %, discards, errors)")
def interfaces(
    f: TrafficFilter = Depends(traffic_filter),
    limit: int = Depends(limit_param),
    sort: Literal["peak", "p95", "avg", "discards"] = Query("peak"),
    db: Database = Depends(get_db),
):
    """Uses `range`/`from`/`to`, `exporter` and `ifindex` filters; other traffic filters do not apply."""
    return utilization.top_interfaces(db, f, limit, sort)


@router.get("/broadcast", summary="Broadcast and multicast per interface (storm detection)")
def broadcast(
    f: TrafficFilter = Depends(traffic_filter),
    limit: int = Depends(limit_param),
    threshold_pps: float = Query(1000, gt=0, le=10_000_000, description="Storm threshold (broadcast packets/s)"),
    db: Database = Depends(get_db),
):
    return utilization.broadcast(db, f, limit, threshold_pps)


@router.get("/timeseries", summary="Utilization of one interface over time (avg and peak per bucket)")
def timeseries(
    exporter: str = Query(..., description="Exporter id"),
    ifindex: int = Query(..., ge=0),
    step: int | None = Query(None, ge=1),
    f: TrafficFilter = Depends(traffic_filter),
    db: Database = Depends(get_db),
):
    if "/" not in exporter:
        raise HTTPException(422, "exporter must be an exporter id (exporter_ip/agent_ip/sub_id)")
    return utilization.timeseries(db, f, exporter, ifindex, step, get_settings().max_timeseries_points)
