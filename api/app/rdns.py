"""Reverse DNS resolution, run by the worker (never on the request path).

Settings (configuration store):
    dns.enabled        "true" (default) | "false"
    dns.servers        comma-separated resolver IPs; empty = the system resolver
    dns.max_per_run    lookups per run (default 300)
"""

from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import dns.exception
import dns.resolver
import dns.reversename

from . import security
from .catalog import ip_expr
from .db import Database

POSITIVE_TTL = timedelta(hours=24)
NEGATIVE_TTL = timedelta(hours=6)
ERROR_TTL = timedelta(hours=1)
TIMEOUT = 2.0
WORKERS = 16

Resolver = Callable[[str], tuple[str, str]]  # ip -> (status, name)


def get_config() -> dict[str, Any]:
    g = security.get_setting
    servers = [s.strip() for s in (g("dns.servers", "") or "").split(",") if s.strip()]
    return {"enabled": (g("dns.enabled", "true") or "true") == "true", "servers": servers,
            "max_per_run": int(g("dns.max_per_run", "300") or 300)}


def save_config(body: dict[str, Any]) -> None:
    if body.get("enabled") is not None:
        security.set_setting("dns.enabled", "true" if body["enabled"] else "false")
    if body.get("servers") is not None:
        servers = [s.strip() for s in body["servers"] if s and s.strip()]
        for s in servers:
            ipaddress.ip_address(s)  # ValueError on invalid input
        if len(servers) > 4:
            raise ValueError("at most 4 DNS servers")
        security.set_setting("dns.servers", ",".join(servers))
    if body.get("max_per_run") is not None:
        n = int(body["max_per_run"])
        if not 10 <= n <= 5000:
            raise ValueError("max_per_run must be 10-5000")
        security.set_setting("dns.max_per_run", str(n))


def make_resolver(servers: list[str]) -> Resolver:
    r = dns.resolver.Resolver(configure=not servers)
    if servers:
        r.nameservers = servers
    r.lifetime = TIMEOUT
    r.timeout = TIMEOUT

    def lookup(ip: str) -> tuple[str, str]:
        try:
            answer = r.resolve(dns.reversename.from_address(ip), "PTR")
            return "ok", str(answer[0]).rstrip(".")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return "nxdomain", ""
        except (dns.exception.DNSException, OSError):
            return "error", ""

    return lookup


def candidates(db: Database, limit: int) -> list[str]:
    """IPs seen in the last hour (most active first) without a valid cache entry."""
    rows = db.query(f"""
        SELECT ip FROM (
            SELECT {ip_expr('src_ip')} AS ip, estimated_bytes AS b FROM {db.name}.flow_records
            WHERE timestamp > now() - INTERVAL 1 HOUR AND src_ip IS NOT NULL
            UNION ALL
            SELECT {ip_expr('dst_ip')} AS ip, estimated_bytes AS b FROM {db.name}.flow_records
            WHERE timestamp > now() - INTERVAL 1 HOUR AND dst_ip IS NOT NULL
        )
        WHERE ip NOT IN (SELECT ip FROM {db.name}.dns_cache FINAL WHERE expires_at > now())
        GROUP BY ip ORDER BY sum(b) DESC LIMIT {{limit:UInt32}}""", {"limit": limit})
    out = []
    for r in rows:
        try:
            a = ipaddress.ip_address(r["ip"])
        except ValueError:
            continue
        # Multicast, broadcast-like and link-local addresses have no useful PTR.
        if a.is_multicast or a.is_unspecified or a.is_link_local or a.is_loopback:
            continue
        out.append(r["ip"])
    return out


def run(db: Database, resolver: Resolver | None = None) -> dict[str, Any]:
    cfg = get_config()
    if not cfg["enabled"]:
        return {"enabled": False}
    ips = candidates(db, cfg["max_per_run"])
    if not ips:
        return {"enabled": True, "resolved": 0, "names": 0}
    lookup = resolver or make_resolver(cfg["servers"])
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lookup, ips))
    now = datetime.now(timezone.utc)
    ttl = {"ok": POSITIVE_TTL, "nxdomain": NEGATIVE_TTL, "error": ERROR_TTL}
    db.insert("dns_cache", [[ip, name, status, now, now + ttl.get(status, ERROR_TTL)] for ip, (status, name) in zip(ips, results)],
              ["ip", "name", "status", "resolved_at", "expires_at"])
    counts = {s: sum(1 for st, _ in results if st == s) for s in ("ok", "nxdomain", "error")}
    return {"enabled": True, "resolved": len(ips), "names": counts["ok"], "no_name": counts["nxdomain"], "errors": counts["error"]}


def stats(db: Database) -> dict[str, Any]:
    try:
        r = db.query(f"""SELECT count() AS cached, countIf(status = 'ok') AS names,
                                countIf(status = 'nxdomain') AS no_name, countIf(status = 'error') AS errors
                         FROM {db.name}.dns_cache FINAL WHERE expires_at > now()""")[0]
        return {k: int(v) for k, v in r.items()}
    except Exception:  # noqa: BLE001
        return {"cached": 0, "names": 0, "no_name": 0, "errors": 0}
