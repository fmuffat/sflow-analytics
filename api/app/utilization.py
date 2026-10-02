"""Port utilization from sFlow interface counters (exact, not sampled).

Counters are cumulative (ifIn/OutOctets, discards, errors). For each poll, the
delta with the previous poll of the same interface gives an average rate over
the polling interval (typically 20 s):

    bps = delta_octets * 8 / delta_t        utilization % = bps / ifSpeed * 100

Negative deltas (counter reset or wrap) and gaps longer than MAX_GAP are ignored.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from . import inventory
from .catalog import exporter_id_expr
from .db import Database
from .filters import TrafficFilter

MAX_GAP_MS = 10 * 60 * 1000
LOOKBACK = timedelta(milliseconds=MAX_GAP_MS)

_DELTA = "greatest(toInt64({c}) - toInt64(lagInFrame({c}) OVER w), 0)"


def _polls(db: Database, f: TrafficFilter, ifindexes: list[int]) -> tuple[str, dict[str, Any]]:
    """Subquery with one row per valid poll: rates and utilization."""
    # Look back one gap before the window so its first poll has a predecessor.
    base = TrafficFilter(start=f.start - LOOKBACK, end=f.end, exporters=f.exporters)
    where, params = base.where()
    params["u_start"] = f.start
    if ifindexes:
        where += " AND ifindex IN {u_ifs:Array(UInt32)}"
        params["u_ifs"] = ifindexes
    sql = f"""
        SELECT exporter_id, ifindex, timestamp, speed_bps, dt_ms, d_in, d_out,
               d_in * 8000 / dt_ms AS in_bps, d_out * 8000 / dt_ms AS out_bps,
               if(speed_bps > 0, least(in_bps / speed_bps * 100, 100), NULL) AS in_pct,
               if(speed_bps > 0, least(out_bps / speed_bps * 100, 100), NULL) AS out_pct,
               d_in_disc, d_out_disc, d_in_err, d_out_err,
               d_in_bc * 1000 / dt_ms AS in_bcast_pps, d_out_bc * 1000 / dt_ms AS out_bcast_pps,
               d_in_mc * 1000 / dt_ms AS in_mcast_pps, d_out_mc * 1000 / dt_ms AS out_mcast_pps,
               d_in_bc, d_out_bc, d_in_mc, d_out_mc, d_in_uc, d_out_uc
        FROM (
            SELECT {exporter_id_expr()} AS exporter_id, ifindex, timestamp, speed_bps,
                   dateDiff('millisecond', lagInFrame(timestamp) OVER w, timestamp) AS dt_ms,
                   toInt64(in_octets) - toInt64(lagInFrame(in_octets) OVER w) AS d_in,
                   toInt64(out_octets) - toInt64(lagInFrame(out_octets) OVER w) AS d_out,
                   {_DELTA.format(c="in_discards")} AS d_in_disc,
                   {_DELTA.format(c="out_discards")} AS d_out_disc,
                   {_DELTA.format(c="in_errors")} AS d_in_err,
                   {_DELTA.format(c="out_errors")} AS d_out_err,
                   {_DELTA.format(c="in_broadcast")} AS d_in_bc,
                   {_DELTA.format(c="out_broadcast")} AS d_out_bc,
                   {_DELTA.format(c="in_multicast")} AS d_in_mc,
                   {_DELTA.format(c="out_multicast")} AS d_out_mc,
                   {_DELTA.format(c="in_ucast")} AS d_in_uc,
                   {_DELTA.format(c="out_ucast")} AS d_out_uc
            FROM {db.name}.interface_counters
            WHERE {where}
            WINDOW w AS (PARTITION BY exporter_ip, agent_ip, agent_sub_id, ifindex ORDER BY timestamp
                         ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
        )
        WHERE timestamp >= {{u_start:DateTime64(3, 'UTC')}}
          AND dt_ms > 0 AND dt_ms <= {MAX_GAP_MS} AND d_in >= 0 AND d_out >= 0"""
    return sql, params


def _r(v: Any, digits: int = 2) -> float | None:
    return None if v is None else round(float(v), digits)


def top_interfaces(db: Database, f: TrafficFilter, limit: int, sort: str) -> dict[str, Any]:
    polls, params = _polls(db, f, f.ifindexes)
    params["limit"] = limit
    order = {"peak": "greatest(ifNull(in_max_pct, 0), ifNull(out_max_pct, 0))",
             "p95": "greatest(ifNull(in_p95_pct, 0), ifNull(out_p95_pct, 0))",
             "avg": "greatest(ifNull(in_avg_pct, 0), ifNull(out_avg_pct, 0))",
             "discards": "in_discards + out_discards"}[sort]
    rows = db.query(f"""
        SELECT exporter_id, ifindex, any(speed_bps) AS speed_bps, count() AS polls,
               avg(in_bps) AS in_avg_bps, max(in_bps) AS in_max_bps,
               avg(out_bps) AS out_avg_bps, max(out_bps) AS out_max_bps,
               avg(in_pct) AS in_avg_pct, quantile(0.95)(in_pct) AS in_p95_pct, max(in_pct) AS in_max_pct,
               avg(out_pct) AS out_avg_pct, quantile(0.95)(out_pct) AS out_p95_pct, max(out_pct) AS out_max_pct,
               sum(d_in_disc) AS in_discards, sum(d_out_disc) AS out_discards,
               sum(d_in_err) AS in_errors, sum(d_out_err) AS out_errors
        FROM ({polls})
        GROUP BY exporter_id, ifindex
        ORDER BY {order} DESC, exporter_id, ifindex
        LIMIT {{limit:UInt32}}""", params)
    exp_names, if_meta = inventory.name_maps(db)
    for r in rows:
        meta = if_meta.get((r["exporter_id"], r["ifindex"]), {})
        r["interface_id"] = f"{r['exporter_id']}/{r['ifindex']}"
        r["exporter_name"] = exp_names.get(r["exporter_id"], r["exporter_id"])
        r["label"] = meta.get("label") or f"ifIndex {r['ifindex']}"
        r["speed_bps"] = int(r["speed_bps"]) or None
        for k in ("in_avg_bps", "in_max_bps", "out_avg_bps", "out_max_bps"):
            r[k] = _r(r[k], 1)
        for k in ("in_avg_pct", "in_p95_pct", "in_max_pct", "out_avg_pct", "out_p95_pct", "out_max_pct"):
            r[k] = _r(r[k])
        for k in ("polls", "in_discards", "out_discards", "in_errors", "out_errors"):
            r[k] = int(r[k])
    return {"query": f.describe(), "estimated": False, "source": "interface counters", "items": rows}


def timeseries(db: Database, f: TrafficFilter, exporter_id: str, ifindex: int, step: int | None,
               max_points: int) -> dict[str, Any]:
    from .traffic import pick_step

    step = pick_step(f.seconds, step, max_points)
    step = max(step, 20)  # never finer than a typical polling interval
    polls, params = _polls(db, replace(f, exporters=[exporter_id]), [ifindex])
    params["step"] = step
    rows = db.query(f"""
        SELECT toStartOfInterval(timestamp, toIntervalSecond({{step:UInt32}}), 'UTC') AS t,
               any(speed_bps) AS speed_bps,
               avg(in_bps) AS in_avg_bps, max(in_bps) AS in_max_bps,
               avg(out_bps) AS out_avg_bps, max(out_bps) AS out_max_bps,
               avg(in_pct) AS in_avg_pct, max(in_pct) AS in_max_pct,
               avg(out_pct) AS out_avg_pct, max(out_pct) AS out_max_pct,
               sum(d_in_disc) AS in_discards, sum(d_out_disc) AS out_discards,
               sum(d_in_err) AS in_errors, sum(d_out_err) AS out_errors,
               avg(in_bcast_pps) AS in_bcast_avg_pps, max(in_bcast_pps) AS in_bcast_max_pps,
               avg(in_mcast_pps) AS in_mcast_avg_pps, max(in_mcast_pps) AS in_mcast_max_pps,
               avg(out_bcast_pps) AS out_bcast_avg_pps, max(out_bcast_pps) AS out_bcast_max_pps,
               avg(out_mcast_pps) AS out_mcast_avg_pps, max(out_mcast_pps) AS out_mcast_max_pps
        FROM ({polls}) GROUP BY t ORDER BY t""", params)
    by_t = {(r["t"] if r["t"].tzinfo else r["t"].replace(tzinfo=timezone.utc)): r for r in rows}
    items = []
    t = datetime.fromtimestamp(int(f.start.timestamp()) // step * step, tz=timezone.utc)
    keys = ("in_avg_bps", "in_max_bps", "out_avg_bps", "out_max_bps",
            "in_avg_pct", "in_max_pct", "out_avg_pct", "out_max_pct",
            "in_bcast_avg_pps", "in_bcast_max_pps", "in_mcast_avg_pps", "in_mcast_max_pps",
            "out_bcast_avg_pps", "out_bcast_max_pps", "out_mcast_avg_pps", "out_mcast_max_pps")
    while t < f.end:
        r = by_t.get(t)
        item: dict[str, Any] = {"t": t.isoformat()}
        # No poll in the bucket: null (unknown), not zero.
        for k in keys:
            item[k] = _r(r[k], 1 if k.endswith("bps") or k.endswith("pps") else 2) if r else None
        for k in ("in_discards", "out_discards", "in_errors", "out_errors"):
            item[k] = int(r[k]) if r else None
        items.append(item)
        t += timedelta(seconds=step)
    speed = next((int(r["speed_bps"]) for r in rows if r["speed_bps"]), None)
    return {"query": f.describe(), "estimated": False, "source": "interface counters",
            "exporter_id": exporter_id, "ifindex": ifindex, "speed_bps": speed,
            "step_seconds": step, "items": items}


def broadcast(db: Database, f: TrafficFilter, limit: int, threshold_pps: float) -> dict[str, Any]:
    """Broadcast and multicast per interface from counters; storm = peak broadcast
    rate (in or out) at or above threshold_pps."""
    polls, params = _polls(db, f, f.ifindexes)
    params.update(limit=limit, thr=threshold_pps)
    rows = db.query(f"""
        SELECT exporter_id, ifindex, any(speed_bps) AS speed_bps, count() AS polls,
               avg(in_bcast_pps) AS in_bcast_avg_pps, max(in_bcast_pps) AS in_bcast_max_pps,
               avg(out_bcast_pps) AS out_bcast_avg_pps, max(out_bcast_pps) AS out_bcast_max_pps,
               avg(in_mcast_pps) AS in_mcast_avg_pps, max(in_mcast_pps) AS in_mcast_max_pps,
               avg(out_mcast_pps) AS out_mcast_avg_pps, max(out_mcast_pps) AS out_mcast_max_pps,
               if(sum(d_in_uc + d_in_mc + d_in_bc) > 0, sum(d_in_bc) * 100 / sum(d_in_uc + d_in_mc + d_in_bc), NULL) AS in_bcast_share,
               if(sum(d_out_uc + d_out_mc + d_out_bc) > 0, sum(d_out_bc) * 100 / sum(d_out_uc + d_out_mc + d_out_bc), NULL) AS out_bcast_share,
               greatest(in_bcast_max_pps, out_bcast_max_pps) >= {{thr:Float64}} AS storm,
               argMax(timestamp, greatest(in_bcast_pps, out_bcast_pps)) AS bcast_peak_at,
               countIf(greatest(in_bcast_pps, out_bcast_pps) >= {{thr:Float64}}) AS storm_polls
        FROM ({polls})
        GROUP BY exporter_id, ifindex
        ORDER BY greatest(in_bcast_max_pps, out_bcast_max_pps) DESC, exporter_id, ifindex
        LIMIT {{limit:UInt32}}""", params)
    exp_names, if_meta = inventory.name_maps(db)
    for r in rows:
        meta = if_meta.get((r["exporter_id"], r["ifindex"]), {})
        r["interface_id"] = f"{r['exporter_id']}/{r['ifindex']}"
        r["exporter_name"] = exp_names.get(r["exporter_id"], r["exporter_id"])
        r["label"] = meta.get("label") or f"ifIndex {r['ifindex']}"
        r["speed_bps"] = int(r["speed_bps"]) or None
        r["polls"], r["storm"] = int(r["polls"]), bool(r["storm"])
        for k in list(r):
            if k.endswith("_pps") or k.endswith("_share"):
                r[k] = _r(r[k], 1)
    return {"query": f.describe(), "estimated": False, "source": "interface counters",
            "threshold_pps": threshold_pps, "storms": sum(1 for r in rows if r["storm"]), "items": rows}
