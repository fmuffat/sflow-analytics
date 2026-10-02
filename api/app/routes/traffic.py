from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import sankey as sankey_mod
from .. import layer2, traffic
from ..export import respond
from ..config import get_settings
from ..db import Database, get_db
from ..filters import TrafficFilter, traffic_filter

router = APIRouter(prefix="/traffic", tags=["traffic"])


Format = Literal["json", "csv"]
FORMAT_Q = Query("json", description="json (default) or csv (items only, for spreadsheets)")


def limit_param(limit: int | None = Query(None, ge=1, description="Number of rows (default 10, max 1000)")) -> int:
    s = get_settings()
    return min(limit or s.default_limit, s.max_limit)


@router.get("/summary", summary="Headline figures: volume, rate, conversations, sources")
def summary(f: TrafficFilter = Depends(traffic_filter), db: Database = Depends(get_db)):
    return traffic.summary(db, f)


@router.get("/timeseries", summary="Estimated traffic over time")
def timeseries(
    f: TrafficFilter = Depends(traffic_filter),
    step: int | None = Query(None, ge=1, description="Bucket size in seconds (default: automatic, ~120 points)"),
    group_by: Literal["service", "protocol", "src_ip", "dst_ip", "vlan", "exporter"] | None = Query(
        None, description="Split into the top N values of this dimension + Other (stacked chart)"),
    top: int = Query(5, ge=1, le=20, description="Number of series when group_by is set"),
    db: Database = Depends(get_db),
):
    if group_by:
        return traffic.timeseries_grouped(db, f, group_by, top, step, get_settings().max_timeseries_points)
    return traffic.timeseries(db, f, step, get_settings().max_timeseries_points)


@router.get("/top-sources", summary="Top source IPs by estimated traffic")
def top_sources(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_ips(db, f, "src", limit), format, "top-sources")


@router.get("/top-destinations", summary="Top destination IPs by estimated traffic")
def top_destinations(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_ips(db, f, "dst", limit), format, "top-destinations")


@router.get("/top-conversations", summary="Top conversations by estimated traffic")
def top_conversations(
    f: TrafficFilter = Depends(traffic_filter),
    limit: int = Depends(limit_param),
    by: Literal["pair", "5tuple", "bidir"] = Query(
        "pair", description="pair: source, destination, protocol, service; 5tuple: also ports; bidir: both directions merged"),
    db: Database = Depends(get_db),
    format: Format = FORMAT_Q,
):
    return respond(traffic.top_conversations(db, f, limit, by), format, "top-conversations")


@router.get("/top-services", summary="Top services (protocol + well-known port)")
def top_services(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_simple(db, f, "service", limit), format, "top-services")


@router.get("/top-protocols", summary="Top IP protocols")
def top_protocols(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_simple(db, f, "protocol", limit), format, "top-protocols")


@router.get("/top-vlans", summary="Top VLANs (null = untagged / unknown)")
def top_vlans(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_simple(db, f, "vlan", limit), format, "top-vlans")


@router.get("/top-exporters", summary="Top exporters (switches)")
def top_exporters(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_simple(db, f, "exporter", limit), format, "top-exporters")


@router.get("/top-interfaces", summary="Top interfaces (ingress + egress)")
def top_interfaces(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param), db: Database = Depends(get_db),
        format: Format = FORMAT_Q):
    return respond(traffic.top_interfaces(db, f, limit), format, "top-interfaces")


@router.get("/flows", summary="Individual flow samples, newest first")
def flows(
    f: TrafficFilter = Depends(traffic_filter),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=100_000),
    db: Database = Depends(get_db),
    format: Format = FORMAT_Q,
):
    return respond(traffic.flows(db, f, limit, offset), format, "flows")


@router.get("/sankey", summary="Flow map: top traffic paths across 2-3 dimensions (Sankey)")
def sankey(
    f: TrafficFilter = Depends(traffic_filter),
    levels: str = Query("src_ip,dst_ip", description="2 or 3 of: " + ", ".join(sankey_mod.ALL_LEVELS)),
    top: int = Query(15, ge=1, le=100, description="Number of paths"),
    metric: Literal["bytes", "packets", "samples"] = Query("bytes"),
    db: Database = Depends(get_db),
):
    lv = [x.strip() for x in levels.split(",") if x.strip()]
    if not 2 <= len(lv) <= 3 or len(set(lv)) != len(lv) or any(x not in sankey_mod.ALL_LEVELS for x in lv):
        raise HTTPException(422, "levels: 2 or 3 distinct values among " + ", ".join(sankey_mod.ALL_LEVELS))
    return sankey_mod.sankey(db, f, lv, top, metric)


@router.get("/top-macs", summary="Top MAC addresses (layer 2) with vendor")
def top_macs(
    f: TrafficFilter = Depends(traffic_filter),
    direction: Literal["src", "dst"] = Query("src"),
    limit: int = Depends(limit_param),
    db: Database = Depends(get_db),
    format: Format = FORMAT_Q,
):
    return respond(layer2.top_macs(db, f, direction, limit), format, f"top-{direction}-macs")


@router.get("/top-ethertypes", summary="Traffic per EtherType (IPv4, IPv6, ARP, LLDP, STP...)")
def top_ethertypes(f: TrafficFilter = Depends(traffic_filter), limit: int = Depends(limit_param),
                   db: Database = Depends(get_db), format: Format = FORMAT_Q):
    return respond(layer2.top_ethertypes(db, f, limit), format, "top-ethertypes")
