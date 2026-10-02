"""Traffic analytics queries. All volumes are estimates from sampled data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import inventory, names, oui
from .catalog import exporter_id_expr, ip_expr, protocol_expr, service_expr
from .db import Database
from .filters import TrafficFilter

STEPS = [10, 30, 60, 300, 600, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400]


def _with() -> str:
    return (f"WITH {service_expr()} AS service, {protocol_expr()} AS protocol_name, "
            f"{exporter_id_expr()} AS exporter_id")


def _pct(part: int, total: int) -> float:
    return round(part * 100.0 / total, 2) if total else 0.0


def totals(db: Database, f: TrafficFilter) -> dict[str, int]:
    where, params = f.where()
    rows = db.query(f"""{_with()}
        SELECT sum(estimated_bytes) AS bytes, sum(estimated_packets) AS packets, count() AS samples
        FROM {db.name}.flow_records WHERE {where}""", params)
    r = rows[0] if rows else {}
    return {"bytes": int(r.get("bytes") or 0), "packets": int(r.get("packets") or 0), "samples": int(r.get("samples") or 0)}


def summary(db: Database, f: TrafficFilter) -> dict[str, Any]:
    """Headline figures for the filtered window."""
    where, params = f.where()
    rows = db.query(f"""{_with()}
        SELECT sum(estimated_bytes) AS bytes, sum(estimated_packets) AS packets, count() AS samples,
               uniqExactIf((src_ip, dst_ip), src_ip IS NOT NULL) AS conversations,
               uniqExact(src_ip) AS sources, uniqExact(dst_ip) AS destinations,
               uniqExact(exporter_id) AS exporters, max(timestamp) AS last_sample
        FROM {db.name}.flow_records WHERE {where}""", params)
    r = rows[0]
    b, p = int(r["bytes"] or 0), int(r["packets"] or 0)
    secs = f.seconds
    last = r["last_sample"] if r["samples"] else None
    item = {
        "bytes": b, "packets": p, "samples": int(r["samples"]),
        "bps": round(b * 8 / secs, 1), "pps": round(p / secs, 3),
        "conversations": int(r["conversations"]), "sources": int(r["sources"]),
        "destinations": int(r["destinations"]), "exporters": int(r["exporters"]),
        "last_sample": last,
    }
    return {"query": f.describe(), "estimated": True, "seconds": secs, "summary": item}


def envelope(f: TrafficFilter, items: list[dict[str, Any]], total: dict[str, int] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"query": f.describe(), "estimated": True}
    if total is not None:
        out["total"] = total
    out.update(extra)
    out["items"] = items
    return out


def _top(db: Database, f: TrafficFilter, select: str, group: str, limit: int, extra_where: str = "") -> list[dict[str, Any]]:
    where, params = f.where()
    params["limit"] = limit
    if extra_where:
        where += f" AND {extra_where}"
    return db.query(f"""{_with()}
        SELECT {select}, sum(estimated_bytes) AS bytes, sum(estimated_packets) AS packets, count() AS samples
        FROM {db.name}.flow_records WHERE {where}
        GROUP BY {group} ORDER BY bytes DESC LIMIT {{limit:UInt32}}""", params)


def _finish(rows: list[dict[str, Any]], total: dict[str, int]) -> list[dict[str, Any]]:
    for r in rows:
        r["bytes"], r["packets"], r["samples"] = int(r["bytes"]), int(r["packets"]), int(r["samples"])
        r["percent"] = _pct(r["bytes"], total["bytes"])
    return rows


def top_ips(db: Database, f: TrafficFilter, direction: str, limit: int) -> dict[str, Any]:
    col = "src_ip" if direction == "src" else "dst_ip"
    total = totals(db, f)
    rows = _top(db, f, f"{ip_expr(col)} AS ip", col, limit, f"{col} IS NOT NULL")
    hosts = names.details_for(db, (r["ip"] for r in rows))
    for r in rows:
        h = hosts.get(r["ip"], {})
        r["name"], r["vendor"], r["mac"] = h.get("name"), h.get("vendor"), h.get("mac")
    return envelope(f, _finish(rows, total), total)


def top_conversations(db: Database, f: TrafficFilter, limit: int, by: str) -> dict[str, Any]:
    if by == "bidir":
        return _bidir_conversations(db, f, limit)
    total = totals(db, f)
    keys = ["src_ip", "dst_ip", "ip_protocol", "service"]
    select = (f"{ip_expr('src_ip')} AS src_ip_s, {ip_expr('dst_ip')} AS dst_ip_s, protocol_name AS protocol, "
              "service, min(timestamp) AS first_seen, max(timestamp) AS last_seen, "
              "groupUniqArray(5)(exporter_id) AS exporters, groupUniqArray(10)(input_ifindex) AS input_ifindexes, "
              "groupUniqArray(10)(output_ifindex) AS output_ifindexes, groupUniqArray(10)(vlan) AS vlans")
    if by == "5tuple":
        keys += ["src_port", "dst_port"]
        select += ", src_port, dst_port"
    rows = _top(db, f, select, ", ".join(keys + ["protocol_name"]), limit, "src_ip IS NOT NULL")
    exp_names, if_meta = inventory.name_maps(db)
    hosts = names.details_for(db, [ip for r in rows for ip in (r["src_ip_s"], r["dst_ip_s"])])
    for r in rows:
        r["src_ip"], r["dst_ip"] = r.pop("src_ip_s"), r.pop("dst_ip_s")
        s, d = hosts.get(r["src_ip"], {}), hosts.get(r["dst_ip"], {})
        r["src_name"], r["dst_name"] = s.get("name"), d.get("name")
        r["src_vendor"], r["dst_vendor"] = s.get("vendor"), d.get("vendor")
        r["exporters"] = [{"id": e, "name": exp_names.get(e, e)} for e in sorted(r["exporters"])]
        r["vlans"] = sorted(r["vlans"])
        for k in ("input_ifindexes", "output_ifindexes"):
            r[k] = sorted(r[k])
    return envelope(f, _finish(rows, total), total, by=by)


def _bidir_conversations(db: Database, f: TrafficFilter, limit: int) -> dict[str, Any]:
    """A<->B conversations: both directions merged, with per-direction volumes.
    A is the lower address of the pair."""
    total = totals(db, f)
    rows = _top(db, f, (
        f"{ip_expr('least(src_ip, dst_ip)')} AS host_a, {ip_expr('greatest(src_ip, dst_ip)')} AS host_b, "
        "protocol_name AS protocol, service, "
        "sumIf(estimated_bytes, src_ip <= dst_ip) AS a_to_b_bytes, sumIf(estimated_bytes, src_ip > dst_ip) AS b_to_a_bytes, "
        "min(timestamp) AS first_seen, max(timestamp) AS last_seen, groupUniqArray(10)(vlan) AS vlans"),
        "least(src_ip, dst_ip), greatest(src_ip, dst_ip), protocol_name, service", limit, "src_ip IS NOT NULL")
    hosts = names.details_for(db, [ip for r in rows for ip in (r["host_a"], r["host_b"])])
    for r in rows:
        a, b = hosts.get(r["host_a"], {}), hosts.get(r["host_b"], {})
        r["host_a_name"], r["host_b_name"] = a.get("name"), b.get("name")
        r["host_a_vendor"], r["host_b_vendor"] = a.get("vendor"), b.get("vendor")
        r["a_to_b_bytes"], r["b_to_a_bytes"] = int(r["a_to_b_bytes"]), int(r["b_to_a_bytes"])
        r["vlans"] = sorted(r["vlans"])
    return envelope(f, _finish(rows, total), total, by="bidir")


GROUP_KEYS = {
    "service": "service",
    "protocol": "protocol_name",
    "src_ip": f"ifNull({ip_expr('src_ip')}, 'Non-IP')",
    "dst_ip": f"ifNull({ip_expr('dst_ip')}, 'Non-IP')",
    "vlan": "ifNull(toString(vlan), 'none')",
    "exporter": "exporter_id",
}


def timeseries_grouped(db: Database, f: TrafficFilter, group_by: str, top: int, step: int | None,
                       max_points: int) -> dict[str, Any]:
    """Traffic over time split by the top N values of a dimension, plus 'Other'."""
    key = GROUP_KEYS[group_by]
    step = pick_step(f.seconds, step, max_points)
    where, params = f.where()
    params.update(step=step, top=top)
    leaders = db.query(f"""{_with()}
        SELECT {key} AS k, sum(estimated_bytes) AS bytes FROM {db.name}.flow_records WHERE {where}
        GROUP BY k ORDER BY bytes DESC LIMIT {{top:UInt32}}""", params)
    names = [str(r["k"]) for r in leaders]
    params["keys"] = names
    rows = db.query(f"""{_with()}
        SELECT toStartOfInterval(timestamp, toIntervalSecond({{step:UInt32}}), 'UTC') AS t,
               if({key} IN {{keys:Array(String)}}, {key}, 'Other') AS k, sum(estimated_bytes) AS bytes
        FROM {db.name}.flow_records WHERE {where} GROUP BY t, k ORDER BY t""", params)
    series = names + (["Other"] if any(r["k"] == "Other" for r in rows) else [])
    cells: dict[tuple[datetime, str], int] = {}
    for r in rows:
        t = r["t"] if r["t"].tzinfo else r["t"].replace(tzinfo=timezone.utc)
        cells[(t, r["k"])] = int(r["bytes"])
    items = []
    t = datetime.fromtimestamp(int(f.start.timestamp()) // step * step, tz=timezone.utc)
    while t < f.end:
        vals = {s: cells.get((t, s), 0) for s in series}
        items.append({"t": t.isoformat(), "bytes": vals, "bps": {s: round(b * 8 / step, 1) for s, b in vals.items()}})
        t += timedelta(seconds=step)
    totals_by = {s: sum(i["bytes"][s] for i in items) for s in series}
    return {"query": f.describe(), "estimated": True, "group_by": group_by, "step_seconds": step,
            "series": [{"name": s, "bytes": totals_by[s]} for s in series], "items": items}


def top_simple(db: Database, f: TrafficFilter, key: str, limit: int) -> dict[str, Any]:
    """Top services, protocols, VLANs or exporters."""
    total = totals(db, f)
    if key == "service":
        rows = _top(db, f, "service", "service", limit)
    elif key == "protocol":
        rows = _top(db, f, "protocol_name AS protocol, ip_protocol", "protocol_name, ip_protocol", limit)
    elif key == "vlan":
        rows = _top(db, f, "vlan", "vlan", limit)
    elif key == "exporter":
        rows = _top(db, f, "exporter_id", "exporter_id", limit)
        exp_by_id = {e["id"]: e for e in inventory.exporters(db)}
        for r in rows:
            e = exp_by_id.get(r["exporter_id"], {})
            r["name"] = e.get("name", r["exporter_id"])
            r["agent_ip"] = e.get("agent_ip")
    else:
        raise ValueError(key)
    return envelope(f, _finish(rows, total), total)


def top_interfaces(db: Database, f: TrafficFilter, limit: int) -> dict[str, Any]:
    """Traffic per (exporter, ifIndex); in = ingress on that port, out = egress."""
    where, params = f.where()
    params["limit"] = limit
    total = totals(db, f)
    rows = db.query(f"""{_with()}
        SELECT exporter_id, pair.1 AS ifindex,
               sumIf(estimated_bytes, pair.2 = 'in') AS in_bytes,
               sumIf(estimated_bytes, pair.2 = 'out') AS out_bytes,
               in_bytes + out_bytes AS bytes,
               sum(estimated_packets) AS packets, count() AS samples
        FROM {db.name}.flow_records
        ARRAY JOIN [(input_ifindex, 'in'), (output_ifindex, 'out')] AS pair
        WHERE {where} AND pair.1 IS NOT NULL
        GROUP BY exporter_id, ifindex ORDER BY bytes DESC LIMIT {{limit:UInt32}}""", params)
    exp_names, if_meta = inventory.name_maps(db)
    for r in rows:
        r["in_bytes"], r["out_bytes"] = int(r["in_bytes"]), int(r["out_bytes"])
        meta = if_meta.get((r["exporter_id"], r["ifindex"]), {})
        r["exporter_name"] = exp_names.get(r["exporter_id"], r["exporter_id"])
        r["interface_id"] = f"{r['exporter_id']}/{r['ifindex']}"
        r["interface_name"] = meta.get("name")
        r["label"] = meta.get("label") or f"ifIndex {r['ifindex']}"
        r["speed_bps"] = meta.get("speed_bps")
    # A sample crossing two known ports counts once per port: percentages are
    # relative to the filtered total and may add up to more than 100 %.
    return envelope(f, _finish(rows, total), total)


def pick_step(seconds: float, requested: int | None, max_points: int) -> int:
    target = seconds / 120
    step = next((s for s in STEPS if s >= target), STEPS[-1])
    if requested:
        step = max(requested, int(seconds / max_points) + 1)
    return step


def timeseries(db: Database, f: TrafficFilter, step: int | None, max_points: int) -> dict[str, Any]:
    step = pick_step(f.seconds, step, max_points)
    where, params = f.where()
    params["step"] = step
    rows = db.query(f"""{_with()}
        SELECT toStartOfInterval(timestamp, toIntervalSecond({{step:UInt32}}), 'UTC') AS t,
               sum(estimated_bytes) AS bytes, sum(estimated_packets) AS packets, count() AS samples
        FROM {db.name}.flow_records WHERE {where}
        GROUP BY t ORDER BY t""", params)
    by_t = {r["t"].replace(tzinfo=timezone.utc) if r["t"].tzinfo is None else r["t"]: r for r in rows}
    # Zero-fill so that charts show gaps as zero traffic.
    epoch = int(f.start.timestamp()) // step * step
    t = datetime.fromtimestamp(epoch, tz=timezone.utc)
    items = []
    while t < f.end:
        r = by_t.get(t)
        b, p = (int(r["bytes"]), int(r["packets"])) if r else (0, 0)
        items.append({"t": t.isoformat(), "bytes": b, "packets": p, "samples": int(r["samples"]) if r else 0,
                      "bps": round(b * 8 / step, 1), "pps": round(p / step, 3)})
        t += timedelta(seconds=step)
    total = {"bytes": sum(i["bytes"] for i in items), "packets": sum(i["packets"] for i in items),
             "samples": sum(i["samples"] for i in items)}
    return envelope(f, items, total, step_seconds=step)


FLOW_COLUMNS = f"""timestamp, exporter_id, {ip_expr('agent_ip')} AS agent_ip_s, input_ifindex, output_ifindex,
    src_mac, dst_mac, ether_type, vlan, ip_version, {ip_expr('src_ip')} AS src_ip_s, {ip_expr('dst_ip')} AS dst_ip_s,
    ip_protocol, protocol_name AS protocol, src_port, dst_port, tcp_flags, service,
    sampled_packet_size, sampling_rate, estimated_bytes, estimated_packets"""


def flows(db: Database, f: TrafficFilter, limit: int, offset: int) -> dict[str, Any]:
    where, params = f.where()
    params.update(limit=limit, offset=offset)
    rows = db.query(f"""{_with()}
        SELECT {FLOW_COLUMNS} FROM {db.name}.flow_records WHERE {where}
        ORDER BY timestamp DESC LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}""", params)
    exp_names, _ = inventory.name_maps(db)
    hosts = names.names_for(db, [ip for r in rows for ip in (r["src_ip_s"], r["dst_ip_s"]) if ip])
    for r in rows:
        r["src_ip"], r["dst_ip"], r["agent_ip"] = r.pop("src_ip_s"), r.pop("dst_ip_s"), r.pop("agent_ip_s")
        r["src_name"], r["dst_name"] = hosts.get(r["src_ip"] or ""), hosts.get(r["dst_ip"] or "")
        r["src_mac_vendor"], r["dst_mac_vendor"] = oui.vendor(r["src_mac"]), oui.vendor(r["dst_mac"])
        r["exporter_name"] = exp_names.get(r["exporter_id"], r["exporter_id"])
    return envelope(f, rows, None, limit=limit, offset=offset)
