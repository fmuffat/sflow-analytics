"""Application configuration store: SQLite on the `app-config` volume.

Holds users, sessions, settings, encrypted secrets and background job state:
small, transactional data. Traffic data and the names used inside analytics
queries stay in ClickHouse.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings

MIGRATIONS = [
    # 1: users, sessions, settings, jobs
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        client TEXT
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        secret INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE jobs (
        name TEXT PRIMARY KEY,
        interval_seconds INTEGER NOT NULL,
        last_started_at TEXT,
        last_finished_at TEXT,
        last_status TEXT,
        last_error TEXT,
        last_duration_ms INTEGER,
        runs INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 2: sign-in log
    """
    CREATE TABLE login_events (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        username TEXT NOT NULL,
        client TEXT,
        ok INTEGER NOT NULL,
        detail TEXT
    );
    CREATE INDEX login_events_at ON login_events(at);
    """,
    # 3: alerting (rules, alerts with state and history)
    """
    CREATE TABLE alert_rules (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        severity TEXT NOT NULL DEFAULT 'warning',
        params TEXT NOT NULL DEFAULT '{}',
        scope TEXT NOT NULL DEFAULT '{}',
        notify TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY,
        rule_id INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
        object_key TEXT NOT NULL,
        subject TEXT NOT NULL,
        link TEXT,
        state TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        value REAL,
        peak REAL,
        threshold REAL,
        opened_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        closed_at TEXT,
        acked_by TEXT,
        acked_at TEXT,
        notified TEXT
    );
    CREATE UNIQUE INDEX alerts_active ON alerts(rule_id, object_key) WHERE state != 'closed';
    CREATE INDEX alerts_opened ON alerts(opened_at);
    """,
    # 4: reports (definitions and generated reports, files in <config>/reports)
    """
    CREATE TABLE report_definitions (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        period TEXT NOT NULL,
        options TEXT NOT NULL DEFAULT '{}',
        delivery TEXT NOT NULL DEFAULT '{}',
        last_period TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE reports (
        id INTEGER PRIMARY KEY,
        definition_id INTEGER REFERENCES report_definitions(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        period TEXT NOT NULL,
        label TEXT,
        period_start TEXT,
        period_end TEXT,
        trigger TEXT NOT NULL,
        created_by TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,
        error TEXT,
        files TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '{}',
        delivery TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX reports_created ON reports(created_at);
    """,
]

_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def db_path() -> Path:
    return Path(get_settings().config_dir) / "sflow.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        # executescript() commits any open transaction first, so the whole
        # migration (schema + version bump) is wrapped inside the script.
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {i};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def connection() -> sqlite3.Connection:
    """One connection per thread (SQLite connections are not thread-safe)."""
    path = db_path()
    conn = getattr(_local, "conns", {}).get(str(path))
    if conn is None:
        conn = _connect(path)
        with _init_lock:
            if str(path) not in _initialized:
                migrate(conn)
                _initialized.add(str(path))
        _local.conns = {**getattr(_local, "conns", {}), str(path): conn}
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in connection().execute(sql, params).fetchall()]


def one(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    r = connection().execute(sql, params).fetchone()
    return dict(r) if r else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    return connection().execute(sql, params).rowcount
