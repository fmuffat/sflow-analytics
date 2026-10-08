"""Background worker: runs scheduled jobs and records their state.

    python -m app.worker

Jobs are registered with @job(name, every_seconds). Each run's status,
duration and last error are stored in the configuration store (table
`jobs`) and shown in Administration. A failing job never stops the worker.
Later milestones add DNS resolution, SmartZone/RUCKUS One polling, alert
evaluation and reports as jobs.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from . import security, store

log = logging.getLogger("worker")


@dataclass
class Job:
    name: str
    every: int
    fn: Callable[[], str | None]
    next_run: float = 0.0


JOBS: dict[str, Job] = {}


def job(name: str, every: int):
    def register(fn: Callable[[], str | None]):
        JOBS[name] = Job(name, every, fn)
        return fn
    return register


@job("purge-expired-sessions", every=600)
def purge_expired_sessions() -> str:
    return (f"{security.purge_sessions()} session(s) removed, "
            f"{security.purge_login_events()} sign-in event(s) older than {security.LOGIN_EVENTS_KEEP_DAYS} days removed")


@job("controller-sync", every=60)
def controller_sync() -> str | None:
    """Runs a controller sync when the configured interval elapsed (checked every minute)."""
    from .db import get_db
    from .enrichment import sync

    if not sync.due():
        last = sync.last_sync()
        if not last or last.get("skipped"):
            return "no source configured"
        return "waiting · last sync " + ("ok" if last.get("ok") else f"failed: {last.get('error', '?')}")
    r = sync.run_sync(get_db())
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "sync failed"))
    if r.get("skipped"):
        return "no source configured"
    return f"{r['switches']} switches, {r['ports']} ports, {r['lldp_neighbors']} LLDP neighbors, {r['clients']} clients"


@job("reverse-dns", every=60)
def reverse_dns() -> str:
    from . import rdns
    from .db import get_db

    r = rdns.run(get_db())
    if not r.get("enabled"):
        return "disabled"
    return f"{r['resolved']} looked up, {r['names']} names" + (f", {r['errors']} errors" if r.get("errors") else "")


@job("alerts", every=60)
def alert_rules() -> str:
    """Evaluates the alert rules, opens/closes alerts and sends notifications."""
    from . import alerts
    from .db import get_db

    r = alerts.evaluate(get_db())
    out = f"{r['rules']} rule(s), {r['active']} active alert(s)"
    if r["opened"] or r["closed"]:
        out += f", {r['opened']} opened, {r['closed']} closed"
    if r["errors"]:
        raise RuntimeError(out + " · " + "; ".join(f"{k}: {v[:300]}" for k, v in r["errors"].items()))
    return out


@job("update-check", every=3600)
def update_check() -> str:
    """Latest published version on GitHub, checked at most once a day (can be disabled)."""
    from . import updates

    s = updates.check()
    if not s["enabled"]:
        return "disabled"
    if s["error"]:  # sites without Internet access: informative, not a job failure
        return f"last check failed ({s['error']}); installed {s['current_version']}"
    if s["development_build"]:
        return f"development build; latest release {s['latest_version']}"
    return (f"{s['latest_version']} available (installed {s['current_version']})" if s["update_available"]
            else f"up to date (latest {s['latest_version']}, installed {s['current_version']})")


@job("history-rollup", every=300)
def history_rollup() -> str:
    """Hourly interface history (kept 3 years) from the raw counters."""
    from . import history
    from .db import get_db

    r = history.rollup(get_db())
    if not r["from"]:
        return "no interface counters yet"
    return f"{r['hours']} hour(s) from {r['from'][:16]} summarized" + ("" if r.get("complete") else " (catching up)")


@job("reports", every=300)
def scheduled_reports() -> str:
    """Weekly and monthly reports when due (from 08:00 local time), e-mail, 90-day history."""
    from .db import get_db
    from .reports import service

    r = service.run_scheduled(get_db())
    out = (f"{len(r['generated'])} generated ({'; '.join(r['generated'])})" if r["generated"] else "nothing due")
    if r["purged"]:
        out += f", {r['purged']} expired report(s) removed"
    if r["errors"]:
        raise RuntimeError(out + " · problems: " + "; ".join(r["errors"]))
    return out


def run_job(j: Job) -> bool:
    started = time.time()
    store.execute("""INSERT INTO jobs (name, interval_seconds, last_started_at) VALUES (?, ?, ?)
                     ON CONFLICT(name) DO UPDATE SET interval_seconds = excluded.interval_seconds,
                     last_started_at = excluded.last_started_at""", (j.name, j.every, security.now_iso()))
    try:
        detail = j.fn()
        ok, err = True, None
    except Exception as exc:  # noqa: BLE001 - a job failure must not stop the worker
        ok, detail, err = False, None, f"{type(exc).__name__}: {exc}"
        log.error("job %s failed: %s\n%s", j.name, err, traceback.format_exc(limit=5))
    ms = int((time.time() - started) * 1000)
    store.execute("""UPDATE jobs SET last_finished_at = ?, last_status = ?, last_error = ?, last_duration_ms = ?,
                     runs = runs + 1, failures = failures + ? WHERE name = ?""",
                  (security.now_iso(), "ok" if ok else "error", err if not ok else detail, ms, 0 if ok else 1, j.name))
    return ok


SCHEMA_TABLES = ("flow_records", "interface_counters", "exporters", "interface_hourly")


def wait_for_schema(stop: threading.Event, timeout: float = 300) -> bool:
    """On a fresh install the collector creates the ClickHouse schema: wait for it
    (at most `timeout` seconds) so that the first job runs do not fail."""
    from .db import get_db

    deadline = time.time() + timeout
    while not stop.is_set() and time.time() < deadline:
        try:
            db = get_db()
            n = db.scalar("SELECT count() FROM system.tables WHERE database = {d:String} AND name IN {t:Array(String)}",
                          {"d": db.name, "t": list(SCHEMA_TABLES)})
            if n == len(SCHEMA_TABLES):
                return True
        except Exception:  # noqa: BLE001 - ClickHouse still starting
            pass
        stop.wait(3)
    log.warning("ClickHouse schema not ready after %ss; starting the jobs anyway", timeout)
    return False


def run_forever(stop: threading.Event) -> None:
    security.ensure_admin()
    wait_for_schema(stop)
    log.info("worker started with jobs: %s", ", ".join(JOBS))
    while not stop.is_set():
        now = time.time()
        for j in JOBS.values():
            if now >= j.next_run:
                run_job(j)
                j.next_run = time.time() + j.every
        stop.wait(max(1.0, min(j.next_run for j in JOBS.values()) - time.time()))
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='{"time":"%(asctime)s","service":"worker","level":"%(levelname)s","msg":%(message)r}')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_forever(stop)


if __name__ == "__main__":
    main()
