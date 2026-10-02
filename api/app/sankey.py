"""Flow map (Sankey): traffic paths across 2 or 3 dimensions.

The top N paths (e.g. source -> service -> destination) by volume are
returned as nodes and links; `coverage_percent` tells how much of the
filtered traffic they represent. Volumes are estimates.
"""

from __future__ import annotations

from typing import Any

from . import inventory, names
from .catalog import ip_expr
from .db import Database
from .filters import TrafficFilter
from .traffic import _with, totals

# level -> (SQL expression, extra WHERE condition)
LEVELS: dict[str, tuple[str, str]] = {
    "src_ip": (ip_expr("src_ip"), "src_ip IS NOT NULL"),
    "dst_ip": (ip_expr("dst_ip"), "dst_ip IS NOT NULL"),
    "service": ("service", ""),
    "protocol": ("protocol_name", ""),
    "vlan": ("ifNull(toString(vlan), 'none')", ""),
    "exporter": ("exporter_id", ""),
    "input_if": ("concat(exporter_id, '#', toString(input_ifindex))", "input_ifindex IS NOT NULL"),
    "output_if": ("concat(exporter_id, '#', toString(output_ifindex))", "output_ifindex IS NOT NULL"),
}
METRICS = {"bytes": "sum(estimated_bytes)", "packets": "sum(estimated_packets)", "samples": "count()"}


GROUP_LEVELS = ("src_group", "dst_group")
ALL_LEVELS = list(LEVELS) + list(GROUP_LEVELS)


def sankey(db: Database, f: TrafficFilter, levels: list[str], top: int, metric: str) -> dict[str, Any]:
    from . import groups

    where, params = f.where()
    exprs, conds = [], []
    ip_groups = groups.list_groups(db, "ip") if any(l in GROUP_LEVELS for l in levels) else []
    sql = groups.Sql("s", params)
    for level in levels:
        if level in GROUP_LEVELS:
            exprs.append(groups.ip_classifier("src_ip" if level == "src_group" else "dst_ip", ip_groups, sql))
        else:
            exprs.append(LEVELS[level][0])
            if LEVELS[level][1]:
                conds.append(LEVELS[level][1])
    if conds:
        where += " AND " + " AND ".join(conds)
    params["top"] = top
    cols = ", ".join(f"{e} AS k{i}" for i, e in enumerate(exprs))
    rows = db.query(f"""{_with()}
        SELECT {cols}, {METRICS[metric]} AS v FROM {db.name}.flow_records WHERE {where}
        GROUP BY {", ".join(f"k{i}" for i in range(len(levels)))}
        ORDER BY v DESC LIMIT {{top:UInt32}}""", params)

    exp_names, if_meta = inventory.name_maps(db) if any(l.endswith("_if") or l == "exporter" for l in levels) else ({}, {})

    ip_values = [str(r[f"k{i}"]) for r in rows for i, l in enumerate(levels) if l in ("src_ip", "dst_ip")]
    hosts = names.names_for(db, ip_values) if ip_values else {}

    def label(level: str, value: str) -> str:
        if level in ("src_ip", "dst_ip") and value in hosts:
            return f"{hosts[value]} ({value})"
        if level.endswith("_if"):
            exp, _, idx = value.rpartition("#")
            meta = if_meta.get((exp, int(idx)), {})
            return f"{exp_names.get(exp, exp)} · {meta.get('label') or 'ifIndex ' + idx}"
        if level == "exporter":
            return exp_names.get(value, value)
        return value

    nodes: dict[str, dict[str, Any]] = {}
    links: dict[tuple[str, str], int] = {}
    shown = 0
    for r in rows:
        v = int(r["v"])
        shown += v
        ids = []
        for i, level in enumerate(levels):
            value = str(r[f"k{i}"])
            nid = f"{level}:{value}"
            ids.append(nid)
            n = nodes.setdefault(nid, {"id": nid, "level": level, "depth": i, "value_key": value,
                                       "label": label(level, value), "value": 0})
            n["value"] += v
        for a, b in zip(ids, ids[1:]):
            links[(a, b)] = links.get((a, b), 0) + v

    total_all = totals(db, f)
    denom = {"bytes": total_all["bytes"], "packets": total_all["packets"], "samples": total_all["samples"]}[metric]
    return {
        "query": f.describe(), "estimated": True, "levels": levels, "metric": metric, "top": top,
        "total": denom, "shown": shown,
        "coverage_percent": round(shown * 100 / denom, 1) if denom else 0.0,
        "nodes": sorted(nodes.values(), key=lambda n: (n["depth"], -n["value"])),
        "links": [{"source": a, "target": b, "value": v} for (a, b), v in sorted(links.items(), key=lambda x: -x[1])],
    }
