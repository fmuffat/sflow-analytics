"""Alerting: rules evaluated every minute by the worker, alerts with state and history.

An alert is identified by (rule, object): one interface, one exporter or one
traffic target. Life cycle: `open` (condition true) → optionally `acknowledged`
by an administrator → `closed` when the condition is no longer true (or closed
by hand). Notifications are sent when an alert opens and when it closes.

Rule kinds (parameters in `params`, optional `scope` = exporter / interface group):
- utilization: average utilization of a port ≥ threshold_pct over window_min (in, out or either)
- discards: discards (and/or errors) of a port ≥ threshold over window_min
- broadcast: average broadcast packets/s of a port ≥ threshold_pps over window_min
- exporter_silent: no sFlow from a switch for silent_min minutes (switches seen in the last 7 days)
- traffic: estimated traffic of a host, IP group or interface group ≥ threshold_bps over window_min
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import groups, inventory, notify, store
from .db import Database
from .filters import TrafficFilter, resolve_groups
from .utilization import _polls

KINDS = ("utilization", "discards", "broadcast", "exporter_silent", "traffic")
SEVERITIES = ("critical", "warning", "info")
CHANNELS = ("email", "webhook", "syslog")
CLEAR_RATIO = 0.9    # hysteresis: a threshold alert closes below 90 % of the threshold
DELAY = timedelta(seconds=30)  # ignore the last seconds (collector batching)
KEEP_CLOSED_DAYS = 180

PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "utilization": {"threshold_pct": 80, "window_min": 5, "direction": "either"},
    "discards": {"threshold": 1000, "window_min": 5, "metric": "discards"},
    "broadcast": {"threshold_pps": 1000, "window_min": 2},
    "exporter_silent": {"silent_min": 5},
    "traffic": {"target": "ip", "value": "", "threshold_bps": 100_000_000, "window_min": 5, "direction": "either"},
}

DEFAULT_RULES = [
    ("Port utilization above 80 %", "utilization", "warning", {"threshold_pct": 80, "window_min": 5}),
    ("Port discards", "discards", "warning", {"threshold": 1000, "window_min": 5, "metric": "discards"}),
    ("Broadcast storm", "broadcast", "critical", {"threshold_pps": 1000, "window_min": 2}),
    ("Switch stopped sending sFlow", "exporter_silent", "critical", {"silent_min": 5}),
]


class AlertError(ValueError):
    pass


def now_iso(t: datetime | None = None) -> str:
    return (t or datetime.now(timezone.utc)).isoformat(timespec="seconds")


# --- rules ----------------------------------------------------------------------------

def _rule_row(r) -> dict[str, Any]:
    d = dict(r)
    for k in ("params", "scope", "notify"):
        d[k] = json.loads(d[k] or "{}")
    d["enabled"] = bool(d["enabled"])
    return d


def ensure_default_rules() -> None:
    if store.one("SELECT id FROM alert_rules LIMIT 1"):
        return
    t = now_iso()
    for name, kind, sev, params in DEFAULT_RULES:
        store.execute("INSERT INTO alert_rules (name, kind, enabled, severity, params, scope, notify, created_at, updated_at) "
                      "VALUES (?, ?, 1, ?, ?, '{}', ?, ?, ?)",
                      (name, kind, sev, json.dumps({**PARAM_DEFAULTS[kind], **params}),
                       json.dumps({"channels": [], "recipients": []}), t, t))


def list_rules() -> list[dict[str, Any]]:
    ensure_default_rules()
    return [_rule_row(r) for r in store.query("SELECT * FROM alert_rules ORDER BY name COLLATE NOCASE")]


def get_rule(rule_id: int) -> dict[str, Any]:
    r = store.one("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))
    if not r:
        raise AlertError("no such rule")
    return _rule_row(r)


def _num(v: Any, name: str, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise AlertError(f"{name} must be a number") from exc
    if not lo <= x <= hi:
        raise AlertError(f"{name} must be between {lo:g} and {hi:g}")
    return x


def validate(kind: str, params: dict[str, Any], scope: dict[str, Any], notify_cfg: dict[str, Any]) -> tuple[dict, dict, dict]:
    if kind not in KINDS:
        raise AlertError(f"kind must be one of {', '.join(KINDS)}")
    p = {**PARAM_DEFAULTS[kind], **{k: v for k, v in params.items() if k in PARAM_DEFAULTS[kind]}}
    if "window_min" in p:
        p["window_min"] = int(_num(p["window_min"], "window_min", 1, 1440))
    if kind == "utilization":
        p["threshold_pct"] = _num(p["threshold_pct"], "threshold_pct", 1, 100)
        if p["direction"] not in ("in", "out", "either"):
            raise AlertError("direction must be in, out or either")
    elif kind == "discards":
        p["threshold"] = int(_num(p["threshold"], "threshold", 1, 1e12))
        if p["metric"] not in ("discards", "errors", "both"):
            raise AlertError("metric must be discards, errors or both")
    elif kind == "broadcast":
        p["threshold_pps"] = _num(p["threshold_pps"], "threshold_pps", 1, 1e9)
    elif kind == "exporter_silent":
        p["silent_min"] = int(_num(p["silent_min"], "silent_min", 1, 10080))
    elif kind == "traffic":
        p["threshold_bps"] = _num(p["threshold_bps"], "threshold_bps", 1, 1e13)
        if p["target"] not in ("ip", "ip_group", "if_group"):
            raise AlertError("target must be ip, ip_group or if_group")
        if p["direction"] not in ("src", "dst", "either"):
            raise AlertError("direction must be src, dst or either")
        if not str(p["value"]).strip():
            raise AlertError("value (IP, subnet or group name) is required")
        p["value"] = str(p["value"]).strip()[:64]
    s = {k: str(v).strip() for k, v in scope.items() if k in ("exporter", "if_group") and str(v or "").strip()}
    channels = [c for c in notify_cfg.get("channels", []) if c in CHANNELS]
    recipients = [str(r).strip() for r in notify_cfg.get("recipients", []) if "@" in str(r)][:50]
    return p, s, {"channels": channels, "recipients": recipients, "notify_resolved": bool(notify_cfg.get("notify_resolved", True))}


def save_rule(body: dict[str, Any], rule_id: int | None = None) -> dict[str, Any]:
    name = str(body.get("name", "")).strip()
    if not 1 <= len(name) <= 80:
        raise AlertError("name must be 1-80 characters")
    sev = body.get("severity", "warning")
    if sev not in SEVERITIES:
        raise AlertError(f"severity must be one of {', '.join(SEVERITIES)}")
    p, s, n = validate(body.get("kind", ""), body.get("params") or {}, body.get("scope") or {}, body.get("notify") or {})
    t = now_iso()
    vals = (name, body["kind"], 1 if body.get("enabled", True) else 0, sev, json.dumps(p), json.dumps(s), json.dumps(n))
    if rule_id is None:
        store.execute("INSERT INTO alert_rules (name, kind, enabled, severity, params, scope, notify, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (*vals, t, t))
        rule_id = store.one("SELECT max(id) AS id FROM alert_rules")["id"]
    else:
        old = get_rule(rule_id)
        store.execute("UPDATE alert_rules SET name=?, kind=?, enabled=?, severity=?, params=?, scope=?, notify=?, updated_at=? "
                      "WHERE id=?", (*vals, t, rule_id))
        if old["kind"] != body["kind"] or not vals[2]:
            _close_rule_alerts(rule_id, "rule changed or disabled")
    return get_rule(rule_id)


def delete_rule(rule_id: int) -> None:
    get_rule(rule_id)
    store.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))


# --- evaluation ------------------------------------------------------------------------

@dataclass
class Hit:
    key: str
    subject: str
    link: str
    value: float
    firing: bool      # condition true (opens or keeps an alert)
    holding: bool     # above the clear level (keeps an open alert)
    message: str


def _iface_scope(db: Database, rule: dict[str, Any]) -> tuple[list[str], set[str] | None]:
    exporters = [rule["scope"]["exporter"]] if rule["scope"].get("exporter") else []
    members = None
    if rule["scope"].get("if_group"):
        m = groups.members_of(db, "if", [rule["scope"]["if_group"]]).get(rule["scope"]["if_group"])
        members = set(m or [])
    return exporters, members


def _labels(db: Database):
    exp_names, if_meta = inventory.name_maps(db)

    def label(eid: str, ifindex: int) -> str:
        meta = if_meta.get((eid, ifindex), {})
        return f"{exp_names.get(eid, eid)} · {meta.get('label') or f'ifIndex {ifindex}'}"
    return exp_names, label


def _iface_link(eid: str, ifindex: int) -> str:
    from urllib.parse import quote
    return f"/interfaces/{quote(eid, safe='')}/{ifindex}?range=1h"


def _port_rows(db: Database, rule: dict[str, Any], now: datetime, select: str, having: str) -> list[dict]:
    w = timedelta(minutes=rule["params"]["window_min"])
    exporters, members = _iface_scope(db, rule)
    polls, params = _polls(db, TrafficFilter(start=now - DELAY - w, end=now - DELAY, exporters=exporters), [])
    rows = db.query(f"""SELECT exporter_id, ifindex, count() AS polls, {select}
                        FROM ({polls}) GROUP BY exporter_id, ifindex HAVING polls >= 2 AND ({having})""", params)
    if members is not None:
        rows = [r for r in rows if f"{r['exporter_id']}/{r['ifindex']}" in members]
    return rows


def eval_utilization(db: Database, rule: dict[str, Any], now: datetime) -> list[Hit]:
    p = rule["params"]
    th, clear = p["threshold_pct"], p["threshold_pct"] * CLEAR_RATIO
    expr = {"in": "avg(in_pct)", "out": "avg(out_pct)", "either": "greatest(ifNull(avg(in_pct), 0), ifNull(avg(out_pct), 0))"}[p["direction"]]
    rows = _port_rows(db, rule, now, f"{expr} AS v, max(speed_bps) AS spd", f"v >= {float(clear)}")
    _, label = _labels(db)
    return [Hit(f"{r['exporter_id']}/{r['ifindex']}", label(r["exporter_id"], r["ifindex"]),
                _iface_link(r["exporter_id"], r["ifindex"]), round(r["v"], 1), r["v"] >= th, True,
                f"Average utilization {r['v']:.1f} % over {p['window_min']} min ({p['direction']}), threshold {th:g} %")
            for r in rows if r["v"] is not None]


def eval_discards(db: Database, rule: dict[str, Any], now: datetime) -> list[Hit]:
    p = rule["params"]
    expr = {"discards": "sum(d_in_disc + d_out_disc)", "errors": "sum(d_in_err + d_out_err)",
            "both": "sum(d_in_disc + d_out_disc + d_in_err + d_out_err)"}[p["metric"]]
    rows = _port_rows(db, rule, now, f"{expr} AS v", f"v >= {int(p['threshold'])}")
    _, label = _labels(db)
    return [Hit(f"{r['exporter_id']}/{r['ifindex']}", label(r["exporter_id"], r["ifindex"]),
                _iface_link(r["exporter_id"], r["ifindex"]), float(r["v"]), True, True,
                f"{int(r['v']):,} {p['metric']} in {p['window_min']} min, threshold {int(p['threshold']):,}")
            for r in rows]


def eval_broadcast(db: Database, rule: dict[str, Any], now: datetime) -> list[Hit]:
    p = rule["params"]
    th, clear = p["threshold_pps"], p["threshold_pps"] * CLEAR_RATIO
    rows = _port_rows(db, rule, now, "greatest(avg(in_bcast_pps), avg(out_bcast_pps)) AS v, "
                      "greatest(max(in_bcast_pps), max(out_bcast_pps)) AS pk", f"v >= {float(clear)}")
    _, label = _labels(db)
    return [Hit(f"{r['exporter_id']}/{r['ifindex']}", label(r["exporter_id"], r["ifindex"]),
                _iface_link(r["exporter_id"], r["ifindex"]), round(r["v"], 1), r["v"] >= th, True,
                f"Broadcast {r['v']:,.0f} packets/s on average over {p['window_min']} min (peak {r['pk']:,.0f}), "
                f"threshold {th:,.0f}")
            for r in rows]


def eval_exporter_silent(db: Database, rule: dict[str, Any], now: datetime) -> list[Hit]:
    p = rule["params"]
    names, _ = _labels(db)
    rows = db.query(f"""SELECT id, last_seen FROM {db.name}.exporters FINAL
                        WHERE last_seen >= {{since:DateTime64(3, 'UTC')}}""", {"since": now - timedelta(days=7)})
    only = rule["scope"].get("exporter")
    hits = []
    for r in rows:
        if only and r["id"] != only:
            continue
        last = r["last_seen"] if r["last_seen"].tzinfo else r["last_seen"].replace(tzinfo=timezone.utc)
        silent = (now - last).total_seconds() / 60
        if silent >= p["silent_min"]:
            from urllib.parse import quote
            hits.append(Hit(r["id"], names.get(r["id"], r["id"]), f"/devices/{quote(r['id'], safe='')}",
                            round(silent, 1), True, True,
                            f"No sFlow received for {silent:.0f} min (last at {last:%Y-%m-%d %H:%M} UTC), "
                            f"threshold {p['silent_min']} min"))
    return hits


def eval_traffic(db: Database, rule: dict[str, Any], now: datetime) -> list[Hit]:
    from . import traffic

    p = rule["params"]
    w = timedelta(minutes=p["window_min"])
    f = TrafficFilter(start=now - DELAY - w, end=now - DELAY,
                      exporters=[rule["scope"]["exporter"]] if rule["scope"].get("exporter") else [])
    v = p["value"]
    if p["target"] == "ip":
        {"src": f.src_ips, "dst": f.dst_ips, "either": f.ips}[p["direction"]].append(v)
    elif p["target"] == "ip_group":
        {"src": f.src_groups, "dst": f.dst_groups, "either": f.groups}[p["direction"]].append(v)
    else:
        f.if_groups.append(v)
    try:
        resolve_groups(f, db)
    except Exception as exc:  # noqa: BLE001 - unknown group: report as rule error
        raise AlertError(str(exc)) from exc
    bps = traffic.summary(db, f)["summary"]["bps"]
    th = p["threshold_bps"]
    if bps < th * CLEAR_RATIO:
        return []
    from urllib.parse import urlencode
    q = {"ip": "ip", "ip_group": "group", "if_group": "if_group"}[p["target"]]
    if p["target"] != "if_group" and p["direction"] != "either":
        q = {"ip": {"src": "src_ip", "dst": "dst_ip"}, "ip_group": {"src": "src_group", "dst": "dst_group"}}[p["target"]][p["direction"]]
    label = {"ip": "Host", "ip_group": "IP group", "if_group": "Interface group"}[p["target"]]
    return [Hit(f"{p['target']}:{v}:{p['direction']}", f"{label} {v}", "/explorer?" + urlencode({"range": "1h", q: v}),
                round(bps, 1), bps >= th, True,
                f"Estimated traffic {bps / 1e6:,.1f} Mb/s over {p['window_min']} min ({p['direction']}), "
                f"threshold {th / 1e6:,.1f} Mb/s")]


EVALUATORS: dict[str, Callable[[Database, dict, datetime], list[Hit]]] = {
    "utilization": eval_utilization, "discards": eval_discards, "broadcast": eval_broadcast,
    "exporter_silent": eval_exporter_silent, "traffic": eval_traffic,
}


# --- engine ----------------------------------------------------------------------------

def _event(rule: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    return {"state": a["state"], "severity": a["severity"], "rule": rule["name"], "subject": a["subject"],
            "message": a["message"], "link": a["link"], "opened_at": a["opened_at"], "closed_at": a.get("closed_at"),
            "value": a["value"], "threshold": a.get("threshold")}


def _notify(rule: dict[str, Any], alert_id: int, sender: Callable = notify.dispatch) -> None:
    ch = rule["notify"].get("channels") or []
    if not ch:
        return
    a = dict(store.one("SELECT * FROM alerts WHERE id = ?", (alert_id,)))
    if a["state"] == "closed" and not rule["notify"].get("notify_resolved", True):
        return
    result = sender(_event(rule, a), ch, rule["notify"].get("recipients") or None)
    prev = json.loads(a["notified"] or "{}")
    prev[a["state"]] = {"at": now_iso(), "result": result}
    store.execute("UPDATE alerts SET notified = ? WHERE id = ?", (json.dumps(prev), alert_id))


def _threshold(rule: dict[str, Any]) -> float | None:
    p = rule["params"]
    for k in ("threshold_pct", "threshold", "threshold_pps", "silent_min", "threshold_bps"):
        if k in p:
            return float(p[k])
    return None


def _close_rule_alerts(rule_id: int, why: str) -> None:
    store.execute("UPDATE alerts SET state = 'closed', closed_at = ?, message = message || ? "
                  "WHERE rule_id = ? AND state != 'closed'", (now_iso(), f" ({why})", rule_id))


def evaluate(db: Database, now: datetime | None = None, sender: Callable = notify.dispatch) -> dict[str, Any]:
    """Evaluates every enabled rule; opens, updates and closes alerts; notifies."""
    now = now or datetime.now(timezone.utc)
    stamp = now_iso(now)
    stats = {"rules": 0, "opened": 0, "closed": 0, "active": 0, "errors": {}}
    for rule in list_rules():
        if not rule["enabled"]:
            continue
        stats["rules"] += 1
        try:
            hits = EVALUATORS[rule["kind"]](db, rule, now)
        except Exception as exc:  # noqa: BLE001 - one failing rule must not stop the others
            stats["errors"][rule["name"]] = f"{type(exc).__name__}: {exc}"
            continue
        active = {a["object_key"]: dict(a) for a in
                  store.query("SELECT * FROM alerts WHERE rule_id = ? AND state != 'closed'", (rule["id"],))}
        seen = set()
        for h in hits:
            a = active.get(h.key)
            if a:
                if h.holding:
                    seen.add(h.key)
                    store.execute("UPDATE alerts SET last_seen_at = ?, value = ?, peak = max(ifnull(peak, 0), ?), "
                                  "message = ?, subject = ? WHERE id = ?",
                                  (stamp, h.value, h.value, h.message, h.subject, a["id"]))
            elif h.firing:
                seen.add(h.key)
                store.execute("INSERT INTO alerts (rule_id, object_key, subject, link, state, severity, message, value, "
                              "peak, threshold, opened_at, last_seen_at) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
                              (rule["id"], h.key, h.subject, h.link, rule["severity"], h.message, h.value, h.value,
                               _threshold(rule), stamp, stamp))
                stats["opened"] += 1
                _notify(rule, store.one("SELECT max(id) AS id FROM alerts")["id"], sender)
        for key, a in active.items():
            if key not in seen:
                store.execute("UPDATE alerts SET state = 'closed', closed_at = ? WHERE id = ?", (stamp, a["id"]))
                stats["closed"] += 1
                _notify(rule, a["id"], sender)
    stats["active"] = store.one("SELECT COUNT(*) AS n FROM alerts WHERE state != 'closed'")["n"]
    cutoff = now_iso(now - timedelta(days=KEEP_CLOSED_DAYS))
    store.execute("DELETE FROM alerts WHERE state = 'closed' AND closed_at < ?", (cutoff,))
    return stats


# --- queries and actions -------------------------------------------------------------------

def list_alerts(state: str = "active", limit: int = 200) -> dict[str, Any]:
    where = {"active": "a.state != 'closed'", "closed": "a.state = 'closed'", "all": "1"}[state]
    rows = store.query(f"""SELECT a.*, r.name AS rule, r.kind FROM alerts a JOIN alert_rules r ON r.id = a.rule_id
                           WHERE {where} ORDER BY CASE a.state WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
                                 CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                                 a.opened_at DESC LIMIT ?""", (limit,))
    items = []
    for r in rows:
        d = dict(r)
        d["notified"] = json.loads(d["notified"] or "{}")
        items.append(d)
    return {"items": items, "counts": counts()}


def counts() -> dict[str, int]:
    rows = store.query("SELECT state, severity, COUNT(*) AS n FROM alerts WHERE state != 'closed' GROUP BY state, severity")
    c = {"open": 0, "acknowledged": 0, "critical": 0, "warning": 0, "info": 0}
    for r in rows:
        c[r["state"]] += r["n"]
        if r["state"] == "open":
            c[r["severity"]] += r["n"]
    return c


def acknowledge(alert_id: int, username: str) -> dict[str, Any]:
    if not store.execute("UPDATE alerts SET state = 'acknowledged', acked_by = ?, acked_at = ? WHERE id = ? AND state = 'open'",
                         (username, now_iso(), alert_id)):
        raise AlertError("no open alert with this id")
    return dict(store.one("SELECT * FROM alerts WHERE id = ?", (alert_id,)))


def close(alert_id: int, username: str) -> dict[str, Any]:
    if not store.execute("UPDATE alerts SET state = 'closed', closed_at = ?, acked_by = coalesce(acked_by, ?) "
                         "WHERE id = ? AND state != 'closed'", (now_iso(), username, alert_id)):
        raise AlertError("no active alert with this id")
    return dict(store.one("SELECT * FROM alerts WHERE id = ?", (alert_id,)))
