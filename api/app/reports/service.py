"""Report definitions, generation, schedule, e-mail delivery and history.

A definition says what goes in a report (sections, scope, monitored links) and how
often it runs: weekly (previous Monday-Sunday, sent on Monday from 08:00) or monthly
(previous calendar month, sent on the 1st from 08:00), in the application time zone.
The worker job `reports` checks every few minutes which definitions are due; a period
missed while the worker was stopped is generated when it comes back (only the last one).
"Report now" generates the last period or a custom one on demand.

Generated files are kept in <config>/reports/<id>.<pdf|xlsx> for KEEP_DAYS days.
Administrators manage definitions; every signed-in user can list and download reports.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import notify, store
from ..config import get_settings
from ..db import Database
from .charts import human_bps, human_bytes
from .data import SECTIONS, ReportOptions, collect, period_bounds

log = logging.getLogger("reports")

PERIODS = ("weekly", "monthly")
FORMATS = ("pdf", "xlsx")
MIME = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
SEND_HOUR = 8               # scheduled reports are produced from 08:00 local time
KEEP_DAYS = 90
STALE_RUNNING = timedelta(minutes=30)
MAX_CUSTOM_DAYS = 366
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OPTION_DEFAULTS: dict[str, Any] = {
    "sections": list(SECTIONS), "exporters": [], "monitored_groups": [], "monitored_interfaces": [],
    "busiest_ports": 10, "top": 10, "warn_pct": 70.0,
}
DELIVERY_DEFAULTS: dict[str, Any] = {"formats": ["pdf", "xlsx"], "email": False, "recipients": []}


class ReportError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tz_name() -> str:
    return get_settings().app_timezone


def reports_dir() -> Path:
    d = Path(get_settings().config_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- definitions ---------------------------------------------------------------------------

def _str_list(v: Any, field: str, limit: int = 500) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ReportError(f"{field} must be a list of strings")
    out: list[str] = []
    for x in (x.strip() for x in v):
        if x and x not in out:
            out.append(x)
    if len(out) > limit:
        raise ReportError(f"{field}: at most {limit} entries")
    return out


def _num(v: Any, field: str, lo: float, hi: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ReportError(f"{field} must be a number") from None
    if not lo <= n <= hi:
        raise ReportError(f"{field} must be between {lo:g} and {hi:g}")
    return n


def validate(d: dict[str, Any]) -> dict[str, Any]:
    name = str(d.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ReportError("name is required (80 characters at most)")
    period = d.get("period")
    if period not in PERIODS:
        raise ReportError("period must be weekly or monthly")
    o = {**OPTION_DEFAULTS, **(d.get("options") or {})}
    sections = _str_list(o["sections"], "sections")
    if not sections or set(sections) - set(SECTIONS):
        raise ReportError(f"sections: choose among {', '.join(SECTIONS)}")
    options = {
        "sections": [s for s in SECTIONS if s in sections],   # canonical order
        "exporters": _str_list(o["exporters"], "exporters"),
        "monitored_groups": _str_list(o["monitored_groups"], "monitored groups"),
        "monitored_interfaces": _str_list(o["monitored_interfaces"], "monitored interfaces"),
        "busiest_ports": int(_num(o["busiest_ports"], "busiest ports", 0, 50)),
        "top": int(_num(o["top"], "top", 5, 50)),
        "warn_pct": _num(o["warn_pct"], "warning threshold", 1, 100),
    }
    dl = {**DELIVERY_DEFAULTS, **(d.get("delivery") or {})}
    formats = [f for f in FORMATS if f in _str_list(dl["formats"], "formats")]
    if not formats:
        raise ReportError("choose at least one format (pdf, xlsx)")
    recipients = _str_list(dl["recipients"], "recipients", 50)
    bad = [r for r in recipients if not EMAIL_RE.match(r)]
    if bad:
        raise ReportError(f"invalid e-mail address: {bad[0]}")
    delivery = {"formats": formats, "email": bool(dl["email"]), "recipients": recipients}
    return {"name": name, "enabled": bool(d.get("enabled", True)), "period": period, "options": options, "delivery": delivery}


def _def_row(r: dict[str, Any]) -> dict[str, Any]:
    d = dict(r)
    d["options"] = {**OPTION_DEFAULTS, **json.loads(d["options"] or "{}")}
    d["delivery"] = {**DELIVERY_DEFAULTS, **json.loads(d["delivery"] or "{}")}
    d["enabled"] = bool(d["enabled"])
    d["next_run"] = next_run(d).isoformat() if d["enabled"] else None
    return d


def list_definitions() -> list[dict[str, Any]]:
    return [_def_row(r) for r in store.query("SELECT * FROM report_definitions ORDER BY name COLLATE NOCASE")]


def get_definition(def_id: int) -> dict[str, Any]:
    r = store.one("SELECT * FROM report_definitions WHERE id = ?", (def_id,))
    if not r:
        raise ReportError("no such report definition")
    return _def_row(r)


def _current_key(period: str, today: date | None = None) -> str:
    """Start of the last complete period: the one the next scheduled run must not redo."""
    return period_bounds(ReportOptions(period=period, tz=tz_name()), today)[0].isoformat()


def save_definition(d: dict[str, Any], def_id: int | None = None) -> dict[str, Any]:
    v = validate(d)
    t = now_iso()
    if def_id is None:
        # The first scheduled report is the next period: "Report now" covers the last one.
        new_id = store.connection().execute(
            "INSERT INTO report_definitions (name, enabled, period, options, delivery, last_period, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (v["name"], int(v["enabled"]), v["period"], json.dumps(v["options"]), json.dumps(v["delivery"]),
             _current_key(v["period"]), t, t)).lastrowid
        return get_definition(int(new_id))
    old = get_definition(def_id)
    last = old["last_period"] if old["period"] == v["period"] else _current_key(v["period"])
    store.execute("UPDATE report_definitions SET name = ?, enabled = ?, period = ?, options = ?, delivery = ?, "
                  "last_period = ?, updated_at = ? WHERE id = ?",
                  (v["name"], int(v["enabled"]), v["period"], json.dumps(v["options"]), json.dumps(v["delivery"]),
                   last, t, def_id))
    return get_definition(def_id)


def delete_definition(def_id: int) -> None:
    get_definition(def_id)
    store.execute("DELETE FROM report_definitions WHERE id = ?", (def_id,))


def next_run(d: dict[str, Any], now: datetime | None = None) -> datetime:
    """When the next scheduled report of a definition is produced (local time)."""
    tz = ZoneInfo(tz_name())
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    start, end, *_ = period_bounds(ReportOptions(period=d["period"], tz=tz_name()), now.date())
    due = end + timedelta(hours=SEND_HOUR)
    if d.get("last_period") != start.isoformat():
        return max(due, now)   # last complete period not produced yet: due (or overdue)
    # last period done: next one ends one week / one month later
    if d["period"] == "weekly":
        nxt = end + timedelta(days=7)
    else:
        nxt = (end.replace(day=28) + timedelta(days=4)).replace(day=1)
    return datetime.combine(nxt.date(), time(SEND_HOUR), tzinfo=tz)


# --- generation -------------------------------------------------------------------------------

def _options(d: dict[str, Any], period: str, start: datetime | None = None, end: datetime | None = None) -> ReportOptions:
    o = d["options"]
    return ReportOptions(
        title=d["name"], period=period, start=start, end=end, tz=tz_name(), sections=tuple(o["sections"]),
        exporters=o["exporters"], monitored_groups=o["monitored_groups"], monitored_interfaces=o["monitored_interfaces"],
        busiest_ports=o["busiest_ports"], top=o["top"], warn_pct=o["warn_pct"])


def custom_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Custom period from local dates, both included."""
    if end < start:
        raise ReportError("the end date is before the start date")
    if (end - start).days + 1 > MAX_CUSTOM_DAYS:
        raise ReportError(f"a custom period is at most {MAX_CUSTOM_DAYS} days")
    tz = ZoneInfo(tz_name())
    return (datetime.combine(start, time(), tzinfo=tz), datetime.combine(end + timedelta(days=1), time(), tzinfo=tz))


def queue(d: dict[str, Any], trigger: str, user: str | None, period: str | None = None,
          start: datetime | None = None, end: datetime | None = None, email: bool | None = None,
          today: date | None = None) -> dict[str, Any]:
    """Creates the report row (status running); `generate` fills it."""
    period = period or d["period"]
    o = _options(d, period, start, end)
    s, e, _, _, label = period_bounds(o, today)
    plan = {"options": {**d["options"]}, "formats": d["delivery"]["formats"], "period": period,
            "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "email": d["delivery"]["email"] if email is None else bool(email),
            "recipients": d["delivery"]["recipients"]}
    rid = store.connection().execute(
        "INSERT INTO reports (definition_id, title, period, label, period_start, period_end, trigger, created_by, "
        "created_at, status, delivery) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
        (d.get("id"), d["name"], period, label, s.isoformat(), e.isoformat(), trigger, user, now_iso(),
         json.dumps({"plan": plan}))).lastrowid
    return get_report(int(rid))


def _summary(r: dict[str, Any]) -> dict[str, Any]:
    """A few figures for the history list and the e-mail."""
    out: dict[str, Any] = {}
    s = r.get("summary")
    if s:
        out.update(bytes=s["bytes"], bytes_change=s["bytes_change"] if s["has_previous"] else None, bps=s["bps"],
                   alerts=s["alerts"], storm_events=s["storm_events"], storm_ports=s["storm_ports"])
    p = r.get("ports")
    if p:
        out["warn_pct"] = p["warn_pct"]
        out["hot_links"] = [f'{x["exporter_name"]} {x["label"]} ({x["p95_pct"]:.0f} %)'
                            for x in p["monitored"] + p["busiest"] if x.get("warn")]
    return out


def _mail_body(rep: dict[str, Any], s: dict[str, Any], formats: list[str]) -> str:
    lines = [rep["title"], rep["label"] or "", ""]
    if "bytes" in s:
        ch = s.get("bytes_change")
        lines.append(f"Traffic volume (estimated): {human_bytes(s['bytes'])}"
                     + (f" ({'+' if ch > 0 else ''}{ch:.0f} % vs previous period)" if ch is not None else ""))
        lines.append(f"Average throughput: {human_bps(s['bps'])}")
        lines.append(f"Alerts: {s['alerts']}" + (f" · broadcast storms: {s['storm_events']} ({s['storm_ports']} ports)"
                                                  if s.get("storm_events") else ""))
    if "hot_links" in s:
        hot = s["hot_links"]
        lines.append(f"Ports above {s['warn_pct']:.0f} % (95th percentile): " + (", ".join(hot) if hot else "none"))
    lines += ["", "The full report is attached (" + ", ".join(f.upper() for f in formats) + ")."]
    base = (notify.settings().get("public_url") or "").rstrip("/")
    if base:
        lines.append(f"Reports: {base}/reports")
    return "\n".join(lines)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "report"


def file_name(rep: dict[str, Any], fmt: str) -> str:
    day = (rep.get("period_start") or rep["created_at"])[:10]
    return f"{_slug(rep['title'])}-{day}.{fmt}"


def generate(db: Database, report_id: int) -> dict[str, Any]:
    """Collects the data, renders the files, sends the e-mail; records the outcome. Never raises."""
    from .pdf import render_pdf
    from .xlsx import render_xlsx

    rep = get_report(report_id)
    plan = rep["delivery"]["plan"]
    files: dict[str, Any] = {}
    delivery: dict[str, Any] = {"plan": plan}
    try:
        start = datetime.fromisoformat(plan["start"]) if plan["start"] else None
        end = datetime.fromisoformat(plan["end"]) if plan["end"] else None
        o = _options({"name": rep["title"], "options": plan["options"]}, plan["period"], start, end)
        today = None
        if plan["period"] != "custom":   # the period decided when queued, even if generated later
            today = datetime.fromisoformat(rep["period_end"]).date()
        data = collect(db, o, today)
        content = {}
        for fmt in plan["formats"]:
            content[fmt] = render_pdf(data) if fmt == "pdf" else render_xlsx(data)
            path = reports_dir() / f"{report_id}.{fmt}"
            path.write_bytes(content[fmt])
            files[fmt] = {"size": len(content[fmt])}
        summary = _summary(data)
        status, error = "ok", None
        if plan["email"]:
            try:
                if not notify.settings()["email"]["enabled"]:
                    raise notify.NotifyError("the e-mail channel is disabled (Administration → Notifications)")
                to = notify.send_mail(f"[sFlow Analytics] {rep['title']} · {rep['label']}",
                                      _mail_body(rep, summary, plan["formats"]), plan["recipients"] or None,
                                      [(file_name(rep, f), MIME[f], content[f]) for f in plan["formats"]])
                delivery["email"] = {"at": now_iso(), "result": "ok", "to": to}
            except notify.NotifyError as exc:
                delivery["email"] = {"at": now_iso(), "result": str(exc)}
                status, error = "ok", f"report generated but not sent: {exc}"
    except Exception as exc:  # noqa: BLE001 - recorded on the report
        log.exception("report %s failed", report_id)
        summary, status, error = {}, "error", f"{type(exc).__name__}: {exc}"
    store.execute("UPDATE reports SET status = ?, error = ?, files = ?, summary = ?, delivery = ?, finished_at = ? "
                  "WHERE id = ?", (status, error, json.dumps(files), json.dumps(summary), json.dumps(delivery),
                                   now_iso(), report_id))
    return get_report(report_id)


# --- history -------------------------------------------------------------------------------

def _report_row(r: dict[str, Any]) -> dict[str, Any]:
    d = dict(r)
    for k in ("files", "summary", "delivery"):
        d[k] = json.loads(d[k] or "{}")
    return d


def get_report(report_id: int) -> dict[str, Any]:
    r = store.one("SELECT * FROM reports WHERE id = ?", (report_id,))
    if not r:
        raise ReportError("no such report")
    return _report_row(r)


def public_report(rep: dict[str, Any], admin: bool) -> dict[str, Any]:
    """Recipients (personal data) and the generation plan are for administrators only."""
    out = {k: v for k, v in rep.items() if k != "delivery"}
    email = rep["delivery"].get("email")
    out["email"] = ({**email} if admin else {k: v for k, v in email.items() if k != "to"}) if email else None
    out["email_planned"] = bool(rep["delivery"].get("plan", {}).get("email"))
    return out


def list_reports(limit: int = 500) -> list[dict[str, Any]]:
    return [_report_row(r) for r in store.query("SELECT * FROM reports ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))]


def report_file(report_id: int, fmt: str) -> tuple[Path, str]:
    rep = get_report(report_id)
    if fmt not in rep["files"]:
        raise ReportError("no such file")
    path = reports_dir() / f"{report_id}.{fmt}"
    if not path.is_file():
        raise ReportError("no such file (expired?)")
    return path, file_name(rep, fmt)


def delete_report(report_id: int) -> None:
    get_report(report_id)
    for fmt in FORMATS:
        (reports_dir() / f"{report_id}.{fmt}").unlink(missing_ok=True)
    store.execute("DELETE FROM reports WHERE id = ?", (report_id,))


def purge(now: datetime | None = None) -> int:
    """Reports older than KEEP_DAYS, their files, and files without a report."""
    limit = ((now or datetime.now(timezone.utc)) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    old = [r["id"] for r in store.query("SELECT id FROM reports WHERE created_at < ?", (limit,))]
    for rid in old:
        delete_report(rid)
    known = {str(r["id"]) for r in store.query("SELECT id FROM reports")}
    for f in reports_dir().iterdir():
        if f.is_file() and f.stem not in known:
            f.unlink(missing_ok=True)
    return len(old)


# --- schedule (worker) ------------------------------------------------------------------------

def due(now: datetime | None = None) -> list[dict[str, Any]]:
    tz = ZoneInfo(tz_name())
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    out = []
    for d in list_definitions():
        if not d["enabled"]:
            continue
        start, end, *_ = period_bounds(ReportOptions(period=d["period"], tz=tz_name()), now.date())
        if d["last_period"] != start.isoformat() and now >= end + timedelta(hours=SEND_HOUR):
            out.append({**d, "_key": start.isoformat(), "_today": now.date()})
    return out


def run_scheduled(db: Database, now: datetime | None = None) -> dict[str, Any]:
    """Worker job: fails reports left running (restart), generates the due ones, purges old ones."""
    stale = (datetime.now(timezone.utc) - STALE_RUNNING).isoformat(timespec="seconds")
    store.execute("UPDATE reports SET status = 'error', error = 'interrupted (service restarted)', finished_at = ? "
                  "WHERE status = 'running' AND created_at < ?", (now_iso(), stale))
    done, errors = [], []
    for d in due(now):
        # marked first: a report that fails is not retried every few minutes ("Report now" can redo it)
        store.execute("UPDATE report_definitions SET last_period = ? WHERE id = ?", (d["_key"], d["id"]))
        rep = queue(d, "schedule", None, today=d["_today"])
        rep = generate(db, rep["id"])
        (errors if rep["status"] == "error" or rep["error"] else done).append(f"{d['name']}: {rep['label']}")
    return {"generated": done, "errors": errors, "purged": purge()}
