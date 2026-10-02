"""Fixtures for API tests against a real ClickHouse.

Set SFLOW_TEST_CLICKHOUSE_HOST (and _PORT, _USER, _PASSWORD) to run them;
otherwise they are skipped. A throw-away database is created with the
collector's schema and a small known dataset (ROWS), then dropped.
"""

from __future__ import annotations

import ipaddress
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("COLLECTOR_STATUS_URL", "http://127.0.0.1:9")  # collector unreachable in tests
# Analytics tests run without authentication; tests/test_auth.py enables it explicitly.
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
import tempfile  # noqa: E402

os.environ.setdefault("CONFIG_DIR", tempfile.mkdtemp(prefix="sflow-config-"))

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
WINDOW = {"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T11:00:00Z"}

EXP_A = ("192.0.2.1", "10.0.0.1", 0)
EXP_B = ("192.0.2.2", "10.0.0.2", 0)


def exp_id(e) -> str:
    return f"{e[0]}/{e[1]}/{e[2]}"


@dataclass
class Row:
    minute: int
    exp: tuple
    src: str | None
    dst: str | None
    proto: int | None
    sport: int | None
    dport: int | None
    vlan: int | None
    in_if: int | None
    out_if: int | None
    size: int
    rate: int
    service: str  # expected service label (oracle for catalog.service_expr)

    @property
    def ts(self) -> datetime:
        return T0 + timedelta(minutes=self.minute)

    @property
    def bytes(self) -> int:
        return self.size * self.rate


ROWS = [
    Row(5, EXP_A, "10.1.1.10", "10.2.2.20", 6, 50000, 443, 10, 1, 48, 1500, 1000, "HTTPS"),
    Row(6, EXP_A, "10.1.1.10", "10.2.2.20", 6, 50001, 443, 10, 1, 48, 1500, 1000, "HTTPS"),
    Row(7, EXP_A, "10.2.2.20", "10.1.1.10", 6, 443, 50000, 10, 48, 1, 1500, 1000, "HTTPS"),
    Row(10, EXP_A, "10.1.1.11", "10.2.2.53", 17, 40000, 53, 20, 2, 48, 100, 1000, "DNS"),
    Row(15, EXP_B, "10.3.3.30", "10.2.2.20", 6, 51000, 445, 30, 5, 24, 1400, 2000, "SMB"),
    Row(20, EXP_B, "2001:db8::1", "2001:db8::2", 17, 51001, 443, 30, 5, 24, 1200, 2000, "QUIC"),
    Row(25, EXP_B, "10.3.3.31", "10.4.4.40", 6, 52000, 9999, 30, 5, 24, 800, 2000, "TCP/9999"),
    Row(30, EXP_A, None, None, None, None, None, 1, 3, None, 64, 1000, "Non-IP"),
    Row(40, EXP_A, "10.1.1.12", "10.2.2.20", 1, None, None, 10, 1, 48, 100, 1000, "ICMP"),
    Row(50, EXP_A, "10.1.1.10", "10.2.2.20", 6, 60000, 61000, 10, 1, 48, 500, 1000, "Unknown"),
    Row(-30, EXP_A, "10.1.1.10", "10.2.2.20", 6, 50000, 443, 10, 1, 48, 1500, 1000, "HTTPS"),  # outside window
]
IN_WINDOW = [r for r in ROWS if 0 <= r.minute < 60]


def _ip6(v: str | None):
    if v is None:
        return None
    a = ipaddress.ip_address(v)
    return ipaddress.IPv6Address("::ffff:" + str(a)) if a.version == 4 else a


def _schema_sql() -> str:
    """All collector migrations, in order (the collector owns the schema)."""
    default = Path(__file__).resolve().parents[2] / "collector/internal/storage/clickhouse/migrations"
    d = Path(os.environ.get("SFLOW_SCHEMA_DIR", default))
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(d.glob("*.sql")))


def _statements(sql: str) -> list[str]:
    out, cur = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        cur.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(cur).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            cur = []
    return out


@pytest.fixture(scope="session")
def testdb():
    host = os.environ.get("SFLOW_TEST_CLICKHOUSE_HOST")
    if not host:
        pytest.skip("SFLOW_TEST_CLICKHOUSE_HOST not set")
    from app.config import Settings
    from app.db import Database

    name = f"sflow_apitest_{int(time.time() * 1000)}"
    settings = Settings(
        clickhouse_host=host,
        clickhouse_http_port=int(os.environ.get("SFLOW_TEST_CLICKHOUSE_PORT", "8123")),
        clickhouse_user=os.environ.get("SFLOW_TEST_CLICKHOUSE_USER", "sflow"),
        clickhouse_password=os.environ.get("SFLOW_TEST_CLICKHOUSE_PASSWORD", ""),
        clickhouse_database="default",
    )
    admin = Database(settings)
    for stmt in _statements(_schema_sql().replace("{{DB}}", name).replace("{{RETENTION_DAYS}}", "3650")):
        admin.client.command(stmt)

    db = Database(settings.model_copy(update={"clickhouse_database": name}))
    cols = ["timestamp", "exporter_ip", "agent_ip", "agent_sub_id", "sample_sequence", "source_id_type",
            "source_id_index", "input_ifindex", "output_ifindex", "src_mac", "dst_mac", "ether_type", "vlan",
            "ip_version", "src_ip", "dst_ip", "ip_protocol", "src_port", "dst_port", "tcp_flags",
            "sampled_packet_size", "sampling_rate", "estimated_bytes", "estimated_packets"]
    data = []
    for i, r in enumerate(ROWS):
        ipv = None if r.src is None else ipaddress.ip_address(r.src).version
        data.append([r.ts, _ip6(r.exp[0]), _ip6(r.exp[1]), r.exp[2], i, 0, r.in_if or 0, r.in_if, r.out_if,
                     "00:11:22:33:44:55", "01:80:c2:00:00:00" if r.src is None else "00:aa:bb:cc:dd:ee",
                     None if r.src is None else (0x0800 if ipv == 4 else 0x86DD), r.vlan, ipv,
                     _ip6(r.src), _ip6(r.dst), r.proto, r.sport, r.dport, 0x18 if r.proto == 6 else None,
                     r.size, r.rate, r.bytes, r.rate])
    db.insert("flow_records", data, cols)

    seen = T0 + timedelta(minutes=50)
    db.insert("exporters", [
        [exp_id(e), _ip6(e[0]), _ip6(e[1]), e[2], T0, seen, 1000, seen] for e in (EXP_A, EXP_B)
    ], ["id", "exporter_ip", "agent_ip", "agent_sub_id", "first_seen", "last_seen", "sample_rate", "updated_at"])
    db.insert("interfaces", [
        [exp_id(EXP_A), 1, T0, seen, 1_000_000_000, True, seen],
        [exp_id(EXP_A), 48, T0, seen, 10_000_000_000, True, seen],
        [exp_id(EXP_B), 24, T0, seen, 0, None, seen],
    ], ["exporter_id", "ifindex", "first_seen", "last_seen", "speed_bps", "oper_up", "updated_at"])

    # Interface counters: EXP_A ifIndex 48 (10 Gb/s), a poll every 20 s from 10:00 to 10:10.
    # In: 1 Gb/s constant. Out: 5 Gb/s during 10:05-10:06, idle otherwise. 3 discards at 10:05:20.
    # A counter reset at 10:08:00 must be ignored.
    crow, t, in_oct, out_oct, disc = [], T0, 10**12, 5 * 10**11, 100
    while t <= T0 + timedelta(minutes=10):
        if t == T0 + timedelta(minutes=8):
            in_oct, out_oct = 1000, 1000  # reset (switch reboot)
        crow.append([t, _ip6(EXP_A[0]), _ip6(EXP_A[1]), EXP_A[2], 48, 6, 10_000_000_000, 1, True, True,
                     in_oct, 0, 0, 0, 0, 0, out_oct, 0, 0, 0, disc, 0])
        t += timedelta(seconds=20)
        in_oct += 1_000_000_000 * 20 // 8
        if T0 + timedelta(minutes=5) < t <= T0 + timedelta(minutes=6):
            out_oct += 5_000_000_000 * 20 // 8
        if t == T0 + timedelta(minutes=5, seconds=20):
            disc += 3
    db.insert("interface_counters", crow, [
        "timestamp", "exporter_ip", "agent_ip", "agent_sub_id", "ifindex", "if_type", "speed_bps", "direction",
        "admin_up", "oper_up", "in_octets", "in_ucast", "in_multicast", "in_broadcast", "in_discards", "in_errors",
        "out_octets", "out_ucast", "out_multicast", "out_broadcast", "out_discards", "out_errors"])

    yield db
    admin.client.command(f"DROP DATABASE IF EXISTS {name}")


@pytest.fixture(scope="session")
def client(testdb):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: testdb
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
