"""Data of a report: everything the PDF and Excel renderers need, for one period and the
previous one (comparison). Pure data, no formatting.

Sections v1: summary, monitored links and busiest ports, applications and protocols, talkers,
conversations, trends (daily or hourly volumes vs previous period), alerts and broadcast storms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .. import groups, history, store, traffic, utilization
from ..db import Database
from ..filters import TrafficFilter

SECTIONS = ("summary", "ports", "applications", "talkers", "conversations", "trends", "alerts")


@dataclass
class ReportOptions:
    title: str = "Network report"
    period: str = "weekly"                  # weekly | monthly | custom
    start: datetime | None = None           # custom period (aware)
    end: datetime | None = None
    tz: str = "UTC"
    sections: tuple[str, ...] = SECTIONS
    exporters: list[str] = field(default_factory=list)   # scope; empty = all switches
    monitored_groups: list[str] = field(default_factory=list)   # interface groups always shown
    monitored_interfaces: list[str] = field(default_factory=list)   # ports chosen one by one (interface ids)
    busiest_ports: int = 10                 # 0 = disabled
    top: int = 10                           # rows in top tables
    warn_pct: float = 70.0                  # 95th percentile utilization highlighted above this


def period_bounds(o: ReportOptions, today: date | None = None) -> tuple[datetime, datetime, datetime, datetime, str]:
    """(start, end, previous start, previous end, label), aware datetimes in the report time zone.
    weekly: last complete Monday-Sunday week; monthly: last complete calendar month."""
    tz = ZoneInfo(o.tz)
    today = today or datetime.now(tz).date()
    if o.period == "custom":
        if not (o.start and o.end) or o.end <= o.start:
            raise ValueError("a custom report needs a start before its end")
        s, e = o.start.astimezone(tz), o.end.astimezone(tz)
        return s, e, s - (e - s), s, f"{s:%Y-%m-%d %H:%M} → {e:%Y-%m-%d %H:%M}"
    if o.period == "weekly":
        monday = today - timedelta(days=today.weekday())
        s_d, e_d = monday - timedelta(days=7), monday
        label = f"Week {s_d.isocalendar().week}, {s_d:%d %b} – {(e_d - timedelta(days=1)):%d %b %Y}"
        p_d = s_d - timedelta(days=7)
    elif o.period == "monthly":
        first = today.replace(day=1)
        e_d = first
        s_d = (first - timedelta(days=1)).replace(day=1)
        p_d = (s_d - timedelta(days=1)).replace(day=1)
        label = f"{s_d:%B %Y}"
    else:
        raise ValueError("period must be weekly, monthly or custom")

    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=tz)
    return at(s_d), at(e_d), at(p_d), at(s_d), label


def _f(o: ReportOptions, a: datetime, b: datetime) -> TrafficFilter:
    return TrafficFilter(start=a.astimezone(timezone.utc), end=b.astimezone(timezone.utc), exporters=list(o.exporters))


def _change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _ports(db: Database, o: ReportOptions, f: TrafficFilter, pf: TrafficFilter) -> dict[str, Any]:
    """Monitored links (interface groups) and busiest ports by 95th percentile."""
    rows = utilization.top_interfaces(db, f, 5000, "p95")["items"]
    prev = {r["interface_id"]: r for r in utilization.top_interfaces(db, pf, 5000, "p95")["items"]}
    members = groups.members_of(db, "if", o.monitored_groups) if o.monitored_groups else {}
    if o.monitored_interfaces:
        members = {**members, "Selected": [i for i in o.monitored_interfaces
                                           if i not in {m for ms in members.values() for m in ms}]}
    monitored_ids = {m for ms in members.values() for m in ms}

    def enrich(r: dict[str, Any], group: str | None = None) -> dict[str, Any]:
        p95 = max(r.get("in_p95_pct") or 0, r.get("out_p95_pct") or 0)
        pr = prev.get(r["interface_id"])
        pp95 = max(pr.get("in_p95_pct") or 0, pr.get("out_p95_pct") or 0) if pr else None
        return {**r, "group": group, "p95_pct": p95, "prev_p95_pct": pp95,
                "max_pct": max(r.get("in_max_pct") or 0, r.get("out_max_pct") or 0),
                "discards": (r.get("in_discards") or 0) + (r.get("out_discards") or 0),
                "errors": (r.get("in_errors") or 0) + (r.get("out_errors") or 0),
                "warn": p95 >= o.warn_pct}

    by_id = {r["interface_id"]: r for r in rows}
    monitored = []
    for g, ms in members.items():
        for m in ms:
            if m in by_id:
                monitored.append(enrich(by_id[m], g))
            else:
                monitored.append({"interface_id": m, "group": g, "missing": True, "label": m.rsplit("/", 1)[-1],
                                  "exporter_name": m.rsplit("/", 1)[0]})
    busiest = [enrich(r) for r in rows if r["interface_id"] not in monitored_ids][: o.busiest_ports] if o.busiest_ports else []
    volumes, with_year = _volumes(db, o, f, monitored) if monitored else ([], False)
    return {"monitored": monitored, "busiest": busiest, "warn_pct": o.warn_pct,
            "volumes": volumes, "volumes_year": with_year,
            "groups": [g for g in members if g != "Selected"], "selected": len(o.monitored_interfaces),
            "missing_groups": sorted(set(o.monitored_groups) - set(members))}


def _volumes(db: Database, o: ReportOptions, f: TrafficFilter, monitored: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Exact volumes of the monitored links from the hourly history (kept 3 years): vs the
    previous period and, for monthly reports, the same month last year. Not for custom periods."""
    if o.period not in ("weekly", "monthly"):
        return [], False
    period = "week" if o.period == "weekly" else "month"
    at = f.start.astimezone(ZoneInfo(o.tz)).date()
    compares = ["previous"] + (["year"] if o.period == "monthly" else [])
    tables = {c: {i["id"]: i for i in history.interfaces_table(db, period, at, o.tz, c, None, None, 5000)["items"]}
              for c in compares}
    out = []
    for m in monitored:
        prev = tables["previous"].get(m["interface_id"])
        year = tables.get("year", {}).get(m["interface_id"])
        cur = (prev or year or {}).get("current", {}).get("total_bytes", 0)
        pb = prev["reference"]["total_bytes"] if prev else None
        yb = year["reference"]["total_bytes"] if year else None
        out.append({"exporter_name": m.get("exporter_name"), "label": m.get("label"), "group": m.get("group"),
                    "interface_id": m["interface_id"], "bytes": cur,
                    "prev_bytes": pb, "prev_change": _change(cur, pb), "year_bytes": yb, "year_change": _change(cur, yb)})
    return out, "year" in tables


def storm_events(rows: list[dict[str, Any]], tz: ZoneInfo) -> list[dict[str, Any]]:
    """Ports in storm grouped into events: same broadcast peak minute = one event."""
    events: dict[str, dict[str, Any]] = {}
    for r in rows:
        at = r["bcast_peak_at"]
        at = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(tz)
        key = at.strftime("%Y-%m-%d %H:%M")
        e = events.setdefault(key, {"at": at, "ports": [], "max_pps": 0.0, "switches": set(), "polls": 0})
        pps = max(r["in_bcast_max_pps"], r["out_bcast_max_pps"])
        e["ports"].append({"switch": r["exporter_name"], "port": r["label"], "pps": pps, "polls": r["storm_polls"]})
        e["max_pps"] = max(e["max_pps"], pps)
        e["switches"].add(r["exporter_name"])
        e["polls"] = max(e["polls"], r["storm_polls"])
    out = []
    for e in sorted(events.values(), key=lambda x: x["at"]):
        e["ports"].sort(key=lambda p: -p["pps"])
        e["switches"] = sorted(e["switches"])
        out.append(e)
    return out


def _trend(db: Database, o: ReportOptions, f: TrafficFilter, pf: TrafficFilter, tz: ZoneInfo) -> dict[str, Any]:
    """Traffic per day (per hour for periods up to 2 days), current vs previous period."""
    seconds = (f.end - f.start).total_seconds()
    step = 3600 if seconds <= 2 * 86400 else 86400

    def buckets(tf: TrafficFilter) -> list[tuple[str, int]]:
        # Hourly series summed per local day: days follow the report time zone, not UTC.
        hours = traffic.timeseries(db, tf, 3600, 2000)["items"]
        out: dict[str, int] = {}
        for h in hours:
            t = datetime.fromisoformat(str(h["t"]).replace("Z", "+00:00")).astimezone(tz)
            key = t.strftime("%a %d %H:00") if step == 3600 else t.strftime("%a %d")
            out[key] = out.get(key, 0) + int(h["bytes"])
        return list(out.items())

    cur, prev = buckets(f), buckets(pf)
    points = []
    for i, (label, b) in enumerate(cur):
        p = prev[i][1] if i < len(prev) else None
        points.append({"label": label, "bytes": b, "bps": round(b * 8 / step, 1), "prev_bytes": p or None})
    return {"step": step, "points": points}


def _alerts(start: datetime, end: datetime) -> list[dict[str, Any]]:
    a, b = start.astimezone(timezone.utc).isoformat(timespec="seconds"), end.astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = store.query("""SELECT a.*, r.name AS rule, r.kind FROM alerts a JOIN alert_rules r ON r.id = a.rule_id
                          WHERE a.opened_at < ? AND (a.closed_at IS NULL OR a.closed_at >= ?)
                          ORDER BY a.opened_at""", (b, a))
    out = []
    for r in rows:
        d = dict(r)
        opened = datetime.fromisoformat(d["opened_at"])
        closed = datetime.fromisoformat(d["closed_at"]) if d["closed_at"] else None
        d["duration_s"] = int(((closed or end.astimezone(timezone.utc)) - opened).total_seconds())
        out.append(d)
    return out


def collect(db: Database, o: ReportOptions, today: date | None = None) -> dict[str, Any]:
    tz = ZoneInfo(o.tz)
    start, end, pstart, pend, label = period_bounds(o, today)
    f, pf = _f(o, start, end), _f(o, pstart, pend)
    s = traffic.summary(db, f)["summary"]
    ps = traffic.summary(db, pf)["summary"]
    out: dict[str, Any] = {
        "title": o.title, "period": o.period, "label": label, "tz": o.tz,
        "start": start, "end": end, "previous_start": pstart, "previous_end": pend,
        "generated_at": datetime.now(tz), "sections": list(o.sections), "options": o,
        "scope": o.exporters or ["all switches"],
    }
    if "summary" in o.sections:
        bc = utilization.broadcast(db, f, 500, 1000)
        out["summary"] = {
            "bytes": s["bytes"], "prev_bytes": ps["bytes"], "bytes_change": _change(s["bytes"], ps["bytes"]),
            "bps": s["bps"], "prev_bps": ps["bps"], "bps_change": _change(s["bps"], ps["bps"]),
            "exporters": s["exporters"], "sources": s["sources"], "destinations": s["destinations"],
            "conversations": s["conversations"], "samples": s["samples"],
            "has_previous": ps["samples"] > 0, "storm_ports": bc["storms"],
            "storm_events": len(storm_events([x for x in bc["items"] if x["storm"]], tz)),
        }
    if "ports" in o.sections:
        out["ports"] = _ports(db, o, f, pf)
    if "applications" in o.sections:
        out["applications"] = traffic.top_simple(db, f, "service", o.top)["items"]
        out["protocols"] = traffic.top_simple(db, f, "protocol", 8)["items"]
    if "talkers" in o.sections:
        out["sources"] = traffic.top_ips(db, f, "src", o.top)["items"]
        out["destinations"] = traffic.top_ips(db, f, "dst", o.top)["items"]
    if "conversations" in o.sections:
        out["conversations"] = traffic.top_conversations(db, f, o.top, "bidir")["items"]
    if "trends" in o.sections:
        out["trend"] = _trend(db, o, f, pf, tz)
    if "alerts" in o.sections:
        out["alerts"] = _alerts(start, end)
        out["storms"] = [r for r in utilization.broadcast(db, f, 500, 1000)["items"] if r["storm"]]
        out["storm_events"] = storm_events(out["storms"], tz)
    if "summary" in out:
        out["summary"]["alerts"] = len(out.get("alerts", []))
    return out
