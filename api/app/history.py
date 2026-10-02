"""Long-term interface history and period comparisons (day / week / month / year).

Raw counters expire after RETENTION_DAYS. The worker job `history-rollup`
summarizes them per interface and hour into `interface_hourly` (kept 3 years):
exact byte volumes, peak and 95th percentile of the 20 s poll rates, discards,
errors, broadcast and multicast. Comparisons read only that table.

Periods are calendar periods in the viewer's time zone (a day starts at local
midnight, a week on Monday). When the current period is still running, deltas
compare it with the same elapsed part of the reference period ("to date"), so
that half a month is not compared with a full month.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import groups, inventory
from .db import Database
from .filters import TrafficFilter
from .utilization import _polls

TABLE = "interface_hourly"
PERIODS = ("day", "week", "month", "year")
COMPARES = ("previous", "week", "year")
CHUNK = timedelta(days=1)
_TZ = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")


class HistoryError(ValueError):
    pass


# --- rollup (worker) --------------------------------------------------------------------

def _hour(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def rollup(db: Database, now: datetime | None = None, max_chunks: int = 45) -> dict[str, Any]:
    """Computes hourly rows from the last stored hour (recomputed: it may have been
    partial) up to now, one day per statement. The first run backfills the whole
    raw retention; later runs only touch the last hours."""
    now = now or datetime.now(timezone.utc)
    last = db.scalar(f"SELECT max(hour) FROM {db.name}.{TABLE}")
    if last and last.year > 1970:
        start = _hour(last.replace(tzinfo=timezone.utc)) - timedelta(hours=1)
    else:
        first = db.scalar(f"SELECT min(timestamp) FROM {db.name}.interface_counters")
        if not first or first.year <= 1970:
            return {"hours": 0, "rows": 0, "from": None, "to": None}
        start = _hour(first.replace(tzinfo=timezone.utc) if first.tzinfo is None else first)
    end = now
    t, chunks, rows = start, 0, 0
    while t < end and chunks < max_chunks:
        t2 = min(t + CHUNK, end)
        polls, params = _polls(db, TrafficFilter(start=t, end=t2), [])
        db.command(f"""
            INSERT INTO {db.name}.{TABLE}
            SELECT toStartOfHour(timestamp) AS hour, exporter_id, ifindex,
                   max(speed_bps), count(), toUInt32(sum(dt_ms) / 1000),
                   sum(d_in), sum(d_out), max(in_bps), max(out_bps),
                   quantile(0.95)(in_bps), quantile(0.95)(out_bps),
                   sum(d_in_disc), sum(d_out_disc), sum(d_in_err), sum(d_out_err),
                   sum(d_in_bc), sum(d_out_bc), sum(d_in_mc), sum(d_out_mc),
                   now64(3)
            FROM ({polls})
            GROUP BY hour, exporter_id, ifindex""", params)
        rows += int(db.scalar(f"SELECT count() FROM {db.name}.{TABLE} WHERE hour >= {{a:DateTime('UTC')}} "
                              f"AND hour < {{b:DateTime('UTC')}}", {"a": t, "b": t2}) or 0)
        t, chunks = t2, chunks + 1
    return {"hours": int((t - start).total_seconds() // 3600), "rows": rows,
            "from": start.isoformat(), "to": t.isoformat(), "complete": t >= end}


def coverage(db: Database) -> dict[str, Any]:
    r = db.query(f"SELECT min(hour) AS first, max(hour) AS last, count() AS rows FROM {db.name}.{TABLE}")[0]
    has = r["rows"] > 0
    return {"first_hour": _iso(r["first"]) if has else None, "last_hour": _iso(r["last"]) if has else None,
            "rows": int(r["rows"]), "kept_days": 1096}


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# --- periods ----------------------------------------------------------------------------

def zone(tz: str) -> ZoneInfo:
    if not _TZ.match(tz or ""):
        raise HistoryError("invalid time zone")
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HistoryError(f"unknown time zone {tz!r}") from exc


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def period_dates(period: str, at: date) -> tuple[date, date]:
    """[start, end) local dates of the calendar period containing `at`."""
    if period == "day":
        return at, at + timedelta(days=1)
    if period == "week":
        s = at - timedelta(days=at.weekday())
        return s, s + timedelta(days=7)
    if period == "month":
        s = at.replace(day=1)
        return s, _add_months(s, 1)
    if period == "year":
        return date(at.year, 1, 1), date(at.year + 1, 1, 1)
    raise HistoryError(f"period must be one of {', '.join(PERIODS)}")


def reference_dates(period: str, compare: str, start: date) -> tuple[date, date]:
    """Period compared with: previous one, same weekday a week before (day only),
    or the same period a year before (52 weeks for days and weeks, to keep weekdays)."""
    if compare not in COMPARES:
        raise HistoryError(f"compare must be one of {', '.join(COMPARES)}")
    if compare == "week":
        if period != "day":
            raise HistoryError("compare=week is only available for period=day")
        s = start - timedelta(days=7)
        return s, s + timedelta(days=1)
    if compare == "year" and period in ("day", "week"):
        s = start - timedelta(weeks=52)
        return s, s + (timedelta(days=1) if period == "day" else timedelta(days=7))
    if compare == "year" or period == "year":
        s = date(start.year - 1, start.month, 1) if period == "month" else date(start.year - 1, 1, 1)
        return s, (_add_months(s, 1) if period == "month" else date(s.year + 1, 1, 1))
    if period == "day":
        return start - timedelta(days=1), start
    if period == "week":
        return start - timedelta(days=7), start
    s = _add_months(start, -1)
    return s, start


def _local(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


@dataclass
class Window:
    start: datetime  # local, aware
    end: datetime

    @property
    def utc(self) -> tuple[datetime, datetime]:
        return self.start.astimezone(timezone.utc), self.end.astimezone(timezone.utc)

    def label(self, period: str) -> str:
        s = self.start
        last = (self.end - timedelta(seconds=1))
        if period == "day":
            return s.strftime("%a %Y-%m-%d")
        if period == "week":
            return f"week {s.isocalendar().week} · {s:%Y-%m-%d} → {last:%Y-%m-%d}"
        if period == "month":
            return s.strftime("%B %Y")
        return s.strftime("%Y")


_BUCKET = {  # ClickHouse key of a bucket in the viewer's time zone, and its Python twin
    "day": ("formatDateTime(hour, '%Y-%m-%d %H', {tz:String})", "%Y-%m-%d %H", "hour"),
    "week": ("formatDateTime(hour, '%Y-%m-%d %H', {tz:String})", "%Y-%m-%d %H", "hour"),
    "month": ("formatDateTime(hour, '%Y-%m-%d', {tz:String})", "%Y-%m-%d", "day"),
    "year": ("formatDateTime(hour, '%Y-%m', {tz:String})", "%Y-%m", "month"),
}


def _buckets(period: str, w: Window, tz: ZoneInfo) -> list[tuple[str, datetime, datetime]]:
    """(key, local start, local end) of each bucket of a window (hour buckets follow DST)."""
    _, fmt, unit = _BUCKET[period]
    out = []
    if unit == "hour":
        t = w.start.astimezone(timezone.utc)
        end = w.end.astimezone(timezone.utc)
        while t < end:
            loc = t.astimezone(tz)
            out.append((loc.strftime(fmt), loc, (t + timedelta(hours=1)).astimezone(tz)))
            t += timedelta(hours=1)
        return out
    d = w.start.date()
    while d < w.end.date():
        nxt = d + timedelta(days=1) if unit == "day" else _add_months(d, 1)
        out.append((d.strftime(fmt), _local(d, tz), _local(nxt, tz)))
        d = nxt
    return out


def windows(period: str, at: date, compare: str, tz: ZoneInfo) -> tuple[Window, Window]:
    cs, ce = period_dates(period, at)
    rs, re_ = reference_dates(period, compare, cs)
    return Window(_local(cs, tz), _local(ce, tz)), Window(_local(rs, tz), _local(re_, tz))


# --- interface selection ----------------------------------------------------------------

def _selection(db: Database, interfaces: list[str], if_group: str | None, exporter: str | None,
               params: dict[str, Any]) -> tuple[str, list[str]]:
    """WHERE fragment on (exporter_id, ifindex) and the selected interface ids."""
    ids = list(interfaces)
    if if_group:
        members = groups.members_of(db, "if", [if_group]).get(if_group)
        if members is None:
            raise HistoryError(f"unknown interface group {if_group!r}")
        ids += members
    for i in ids:
        if not groups._IF_MEMBER.match(i):
            raise HistoryError(f"invalid interface id {i!r} (expected exporter_ip/agent_ip/sub_id/ifindex)")
    clauses = []
    if ids or if_group:
        params["sel_ids"] = sorted(set(ids))
        clauses.append("concat(exporter_id, '/', toString(ifindex)) IN {sel_ids:Array(String)}")
    if exporter:
        params["sel_exp"] = exporter
        clauses.append("exporter_id = {sel_exp:String}")
    return (" AND ".join(clauses) or "1"), ids


def _labels(db: Database) -> tuple[dict[str, str], dict[tuple[str, int], dict[str, Any]]]:
    return inventory.name_maps(db)


# --- comparison -------------------------------------------------------------------------

def _pct(cur: float, ref: float) -> float | None:
    return None if not ref else round((cur - ref) / ref * 100, 1)


def _sums(db: Database, where: str, params: dict[str, Any], a: datetime, b: datetime, key: str) -> dict[str, dict]:
    """Per bucket: volumes, average and peak rate, peak utilization; summed over the
    selected interfaces (peak of a set = sum of each interface's peak: an upper bound)."""
    params = {**params, "a": a, "b": b}
    rows = db.query(f"""
        SELECT bkey, sum(in_b) AS in_bytes, sum(out_b) AS out_bytes, max(secs) AS seconds,
               sum(in_pk) AS in_peak_bps, sum(out_pk) AS out_peak_bps, sum(spd) AS speed_bps,
               sum(in_disc) AS in_discards, sum(out_disc) AS out_discards,
               sum(in_err) AS in_errors, sum(out_err) AS out_errors, count() AS interfaces
        FROM (
            SELECT {key} AS bkey, exporter_id, ifindex,
                   sum(in_bytes) AS in_b, sum(out_bytes) AS out_b, sum(covered_seconds) AS secs,
                   max(in_max_bps) AS in_pk, max(out_max_bps) AS out_pk, max(speed_bps) AS spd,
                   sum(in_discards) AS in_disc, sum(out_discards) AS out_disc,
                   sum(in_errors) AS in_err, sum(out_errors) AS out_err
            FROM {db.name}.{TABLE} FINAL
            WHERE hour >= {{a:DateTime('UTC')}} AND hour < {{b:DateTime('UTC')}} AND {where}
            GROUP BY bkey, exporter_id, ifindex)
        GROUP BY bkey""", params)
    return {r["bkey"]: r for r in rows}


def _point(r: dict | None) -> dict[str, Any] | None:
    if not r:
        return None
    secs = r["seconds"] or 0
    spd = r["speed_bps"] or 0
    return {
        "in_bytes": int(r["in_bytes"]), "out_bytes": int(r["out_bytes"]),
        "in_avg_bps": round(r["in_bytes"] * 8 / secs, 1) if secs else None,
        "out_avg_bps": round(r["out_bytes"] * 8 / secs, 1) if secs else None,
        "in_peak_bps": round(r["in_peak_bps"], 1), "out_peak_bps": round(r["out_peak_bps"], 1),
        "in_peak_pct": round(min(r["in_peak_bps"] / spd * 100, 100), 2) if spd else None,
        "out_peak_pct": round(min(r["out_peak_bps"] / spd * 100, 100), 2) if spd else None,
        "discards": int(r["in_discards"] + r["out_discards"]), "errors": int(r["in_errors"] + r["out_errors"]),
        "seconds": int(secs),
    }


def _totals(points: list[dict | None], buckets: list[tuple], upto: datetime | None) -> dict[str, Any]:
    """Totals over the buckets that start before `upto` (all when None)."""
    t = {"in_bytes": 0, "out_bytes": 0, "in_peak_bps": 0.0, "out_peak_bps": 0.0, "discards": 0, "errors": 0}
    secs = 0.0
    has = False
    for p, (_, bstart, _end) in zip(points, buckets):
        if upto is not None and bstart >= upto:
            break
        if not p:
            continue
        has = True
        for k in ("in_bytes", "out_bytes", "discards", "errors"):
            t[k] += p[k]
        t["in_peak_bps"] = max(t["in_peak_bps"], p["in_peak_bps"])
        t["out_peak_bps"] = max(t["out_peak_bps"], p["out_peak_bps"])
        secs += p["seconds"]
    t["in_avg_bps"] = round(t["in_bytes"] * 8 / secs, 1) if secs else None
    t["out_avg_bps"] = round(t["out_bytes"] * 8 / secs, 1) if secs else None
    t["has_data"] = has
    return t


def compare(db: Database, period: str, at: date | None, tz_name: str, compare_to: str,
            interfaces: list[str], if_group: str | None, now: datetime | None = None) -> dict[str, Any]:
    """Two aligned series (current and reference period) for one interface or a set."""
    tz = zone(tz_name)
    now = now or datetime.now(timezone.utc)
    if period not in PERIODS:
        raise HistoryError(f"period must be one of {', '.join(PERIODS)}")
    if not interfaces and not if_group:
        raise HistoryError("choose an interface or an interface group")
    at = at or now.astimezone(tz).date()
    cur, ref = windows(period, at, compare_to, tz)
    params: dict[str, Any] = {"tz": tz_name}
    where, ids = _selection(db, interfaces, if_group, None, params)
    key = _BUCKET[period][0]
    cb, rb = _buckets(period, cur, tz), _buckets(period, ref, tz)
    cs = _sums(db, where, params, *cur.utc, key)
    rs = _sums(db, where, params, *ref.utc, key)
    cur_pts = [_point(cs.get(k)) for k, _, _ in cb]
    ref_pts = [_point(rs.get(k)) for k, _, _ in rb]

    running = cur.start <= now.astimezone(tz) < cur.end
    elapsed = now.astimezone(tz) - cur.start if running else None
    cur_tot = _totals(cur_pts, cb, None)
    ref_full = _totals(ref_pts, rb, None)
    ref_same = _totals(ref_pts, rb, ref.start + elapsed) if elapsed is not None else ref_full
    deltas = {k: _pct((cur_tot[k] or 0), (ref_same[k] or 0))
              for k in ("in_bytes", "out_bytes", "in_avg_bps", "out_avg_bps", "in_peak_bps", "out_peak_bps")}

    n = max(len(cb), len(rb))
    points = []
    for i in range(n):
        c = cb[i] if i < len(cb) else None
        r = rb[i] if i < len(rb) else None
        points.append({"index": i,
                       "label": _bucket_label(period, (c or r)[1]),
                       "current_start": c[1].isoformat() if c else None,
                       "reference_start": r[1].isoformat() if r else None,
                       "current": cur_pts[i] if c else None,
                       "reference": ref_pts[i] if r else None})
    exp_names, if_meta = _labels(db)
    return {
        "period": period, "compare": compare_to, "tz": tz_name,
        "bucket": _BUCKET[period][2],
        "interfaces": [_if_label(i, exp_names, if_meta) for i in sorted(set(ids))],
        "if_group": if_group,
        "current": {"from": cur.start.isoformat(), "to": cur.end.isoformat(), "label": cur.label(period),
                    "running": running, "totals": cur_tot},
        "reference": {"from": ref.start.isoformat(), "to": ref.end.isoformat(), "label": ref.label(period),
                      "totals": ref_full, "totals_same_elapsed": ref_same},
        "delta_pct": deltas,
        "delta_basis": "same elapsed time" if running else "full periods",
        "navigation": _navigation(period, cur, tz),
        "coverage": coverage(db),
        "points": points,
    }


def _bucket_label(period: str, start: datetime) -> str:
    if period == "day":
        return start.strftime("%H:00")
    if period == "week":
        return start.strftime("%a %H:00")
    if period == "month":
        return start.strftime("%d")
    return start.strftime("%b")


def _navigation(period: str, cur: Window, tz: ZoneInfo) -> dict[str, str]:
    prev_day = (cur.start - timedelta(days=1)).date()
    return {"previous_at": prev_day.isoformat(), "next_at": cur.end.date().isoformat()}


def _if_label(iid: str, exp_names: dict[str, str], if_meta: dict) -> dict[str, Any]:
    m = groups._IF_MEMBER.match(iid)
    exp, idx = (m.group(1), int(m.group(2))) if m else (iid, 0)
    meta = if_meta.get((exp, idx), {})
    return {"id": iid, "exporter_id": exp, "ifindex": idx, "exporter_name": exp_names.get(exp, exp),
            "label": meta.get("label") or f"ifIndex {idx}"}


def interfaces_table(db: Database, period: str, at: date | None, tz_name: str, compare_to: str,
                     if_group: str | None, exporter: str | None, limit: int,
                     now: datetime | None = None) -> dict[str, Any]:
    """Every interface (or a group / an exporter): this period vs the reference period."""
    tz = zone(tz_name)
    now = now or datetime.now(timezone.utc)
    if period not in PERIODS:
        raise HistoryError(f"period must be one of {', '.join(PERIODS)}")
    at = at or now.astimezone(tz).date()
    cur, ref = windows(period, at, compare_to, tz)
    running = cur.start <= now.astimezone(tz) < cur.end
    ref_end = ref.end
    if running:
        ref_end = min(ref.end, ref.start + (now.astimezone(tz) - cur.start))
    params: dict[str, Any] = {"limit": limit}
    where, _ = _selection(db, [], if_group, exporter, params)
    (ca, cb_), (ra, rb_) = cur.utc, (ref.start.astimezone(timezone.utc), ref_end.astimezone(timezone.utc))
    params.update({"ca": ca, "cb": cb_, "ra": ra, "rb": rb_})
    rows = db.query(f"""
        SELECT exporter_id, ifindex, max(speed_bps) AS speed_bps,
               sumIf(in_bytes, cur) AS cur_in, sumIf(out_bytes, cur) AS cur_out,
               sumIf(covered_seconds, cur) AS cur_secs,
               maxIf(greatest(in_max_bps, out_max_bps), cur) AS cur_peak,
               sumIf(in_bytes, NOT cur) AS ref_in, sumIf(out_bytes, NOT cur) AS ref_out,
               sumIf(covered_seconds, NOT cur) AS ref_secs,
               maxIf(greatest(in_max_bps, out_max_bps), NOT cur) AS ref_peak,
               sumIf(in_discards + out_discards, cur) AS cur_discards
        FROM (
            SELECT *, hour >= {{ca:DateTime('UTC')}} AS cur
            FROM {db.name}.{TABLE} FINAL
            WHERE ((hour >= {{ca:DateTime('UTC')}} AND hour < {{cb:DateTime('UTC')}})
                OR (hour >= {{ra:DateTime('UTC')}} AND hour < {{rb:DateTime('UTC')}})) AND {where})
        GROUP BY exporter_id, ifindex
        HAVING cur_in + cur_out + ref_in + ref_out > 0
        ORDER BY cur_in + cur_out DESC, exporter_id, ifindex
        LIMIT {{limit:UInt32}}""", params)
    exp_names, if_meta = _labels(db)
    items = []
    for r in rows:
        iid = f"{r['exporter_id']}/{r['ifindex']}"
        spd = int(r["speed_bps"]) or None
        cur_total, ref_total = int(r["cur_in"] + r["cur_out"]), int(r["ref_in"] + r["ref_out"])
        items.append({
            **_if_label(iid, exp_names, if_meta), "speed_bps": spd,
            "current": {"in_bytes": int(r["cur_in"]), "out_bytes": int(r["cur_out"]), "total_bytes": cur_total,
                        "in_avg_bps": round(r["cur_in"] * 8 / r["cur_secs"], 1) if r["cur_secs"] else None,
                        "out_avg_bps": round(r["cur_out"] * 8 / r["cur_secs"], 1) if r["cur_secs"] else None,
                        "peak_bps": round(r["cur_peak"], 1),
                        "peak_pct": round(min(r["cur_peak"] / spd * 100, 100), 2) if spd else None,
                        "discards": int(r["cur_discards"])},
            "reference": {"in_bytes": int(r["ref_in"]), "out_bytes": int(r["ref_out"]), "total_bytes": ref_total,
                          "peak_bps": round(r["ref_peak"], 1),
                          "peak_pct": round(min(r["ref_peak"] / spd * 100, 100), 2) if spd else None},
            "delta_pct": _pct(cur_total, ref_total),
            "peak_delta_pct": _pct(r["cur_peak"], r["ref_peak"]),
        })
    return {
        "period": period, "compare": compare_to, "tz": tz_name,
        "current": {"from": cur.start.isoformat(), "to": cur.end.isoformat(), "label": cur.label(period), "running": running},
        "reference": {"from": ref.start.isoformat(), "to": ref.end.isoformat(), "label": ref.label(period),
                      "compared_until": ref_end.isoformat()},
        "delta_basis": "same elapsed time" if running else "full periods",
        "navigation": _navigation(period, cur, tz),
        "coverage": coverage(db),
        "items": items,
    }
