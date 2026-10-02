"""ClickHouse access. All queries use server-side parameters ({name:Type})."""

from __future__ import annotations

import re
import threading
from typing import Any

import clickhouse_connect

from .config import Settings, get_settings

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_lock = threading.Lock()


class Database:
    """Thin wrapper so that tests can point the API at another database."""

    def __init__(self, settings: Settings):
        if not _IDENT.match(settings.clickhouse_database):
            raise ValueError(f"invalid database name {settings.clickhouse_database!r}")
        self.name = settings.clickhouse_database
        self.client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_http_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            # Sessions disabled: the client can then be shared between threads.
            autogenerate_session_id=False,
            settings={"max_execution_time": settings.query_timeout_seconds},
            connect_timeout=5,
            send_receive_timeout=settings.query_timeout_seconds + 5,
        )

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self.client.query(sql, parameters=params or {}).named_results())

    def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        rows = self.client.query(sql, parameters=params or {}).result_rows
        return rows[0][0] if rows else None

    def command(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Statement without result set (INSERT ... SELECT, ALTER...)."""
        return self.client.command(sql, parameters=params or {})

    def insert(self, table: str, rows: list[list[Any]], columns: list[str]) -> None:
        """Insert into a table of this database."""
        self.client.insert(table, rows, column_names=columns, database=self.name)

    def ping(self) -> bool:
        return bool(self.client.ping())


_db: Database | None = None


def get_db() -> Database:
    """FastAPI dependency; one shared client per process."""
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                _db = Database(get_settings())
    return _db
