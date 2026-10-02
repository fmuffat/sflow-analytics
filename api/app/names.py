"""Endpoint names: manual aliases, controller clients, reverse DNS.

Priority for an IP (first match wins):
    1. manual alias on the IP
    2. manual alias on its MAC (MAC learned from the controller or recent flows)
    3. name known by the controller (clients seen on switch ports)
    4. reverse DNS (cache filled by the worker)
    5. manual alias on a subnet containing the IP
"""

from __future__ import annotations

import csv
import io
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from . import oui
from .db import Database

KINDS = ("ip", "mac", "cidr")
_MAC = re.compile(r"^([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})[:\-.]?([0-9a-f]{2})$")


class AliasError(ValueError):
    pass


def normalize_key(kind: str, key: str) -> str:
    k = (key or "").strip().lower()
    if kind == "ip":
        try:
            return str(ipaddress.ip_address(k))
        except ValueError as exc:
            raise AliasError(f"invalid IP address: {key!r}") from exc
    if kind == "cidr":
        try:
            return str(ipaddress.ip_network(k, strict=False))
        except ValueError as exc:
            raise AliasError(f"invalid subnet: {key!r}") from exc
    if kind == "mac":
        m = _MAC.match(k.replace(" ", ""))
        if not m:
            raise AliasError(f"invalid MAC address: {key!r}")
        return ":".join(m.groups())
    raise AliasError(f"kind must be one of {', '.join(KINDS)}")


def normalize_name(name: str) -> str:
    n = (name or "").strip()
    if not n or len(n) > 64:
        raise AliasError("name must be 1-64 characters")
    return n


# --- aliases ---------------------------------------------------------------------

def list_aliases(db: Database) -> list[dict[str, Any]]:
    return db.query(f"""SELECT kind, key, name, notes, updated_at FROM {db.name}.host_aliases FINAL
                        WHERE deleted = 0 ORDER BY kind, key""")


def set_alias(db: Database, kind: str, key: str, name: str, notes: str = "") -> dict[str, str]:
    k = normalize_key(kind, key)
    row = {"kind": kind, "key": k, "name": normalize_name(name), "notes": (notes or "").strip()[:500]}
    db.insert("host_aliases", [[kind, k, row["name"], row["notes"], 0, datetime.now(timezone.utc)]],
              ["kind", "key", "name", "notes", "deleted", "updated_at"])
    return row


def delete_alias(db: Database, kind: str, key: str) -> None:
    k = normalize_key(kind, key)
    db.insert("host_aliases", [[kind, k, "", "", 1, datetime.now(timezone.utc)]],
              ["kind", "key", "name", "notes", "deleted", "updated_at"])


def import_csv(db: Database, text: str) -> dict[str, Any]:
    """CSV lines: kind,key,name[,notes] (header optional). Invalid lines are reported, not fatal."""
    rows, errors = [], []
    for n, rec in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not rec or not any(c.strip() for c in rec) or rec[0].strip().startswith("#"):
            continue
        if n == 1 and rec[0].strip().lower() == "kind":
            continue
        try:
            if len(rec) < 3:
                raise AliasError("expected kind,key,name[,notes]")
            kind = rec[0].strip().lower()
            if kind not in KINDS:
                raise AliasError(f"kind must be one of {', '.join(KINDS)}")
            rows.append([kind, normalize_key(kind, rec[1]), normalize_name(rec[2]),
                         (rec[3] if len(rec) > 3 else "").strip()[:500], 0, datetime.now(timezone.utc)])
        except AliasError as exc:
            errors.append({"line": n, "error": str(exc)})
    if rows:
        db.insert("host_aliases", rows, ["kind", "key", "name", "notes", "deleted", "updated_at"])
    return {"imported": len(rows), "errors": errors[:50], "error_count": len(errors)}


def export_csv(db: Database) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["kind", "key", "name", "notes"])
    for a in list_aliases(db):
        w.writerow([a["kind"], a["key"], a["name"], a["notes"]])
    return buf.getvalue()


# --- resolution --------------------------------------------------------------------

def _q(db: Database, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    try:
        return db.query(sql, params or {})
    except Exception:  # noqa: BLE001 - tables may not exist yet: no names then
        return []


# A MAC seen with more distinct IPs than this in a day is a router/gateway
# (it carries traffic of other hosts): it must not name or identify those IPs.
GATEWAY_MAC_MIN_IPS = 16


def ip_macs(db: Database, ips: list[str]) -> dict[str, str]:
    """IP -> MAC of the device itself: controller clients first, then the source MAC
    of recent flows, excluding gateway MACs."""
    from .catalog import ip_expr

    if not ips:
        return {}
    # Aliases must not reuse column names used in WHERE (ClickHouse would bind the aggregate).
    macs = {r["ip"]: r["mac_addr"] for r in _q(db, f"""
        SELECT ip, argMax(mac, synced_at) AS mac_addr FROM {db.name}.ctrl_clients FINAL
        WHERE ip IN {{ips:Array(String)}} AND mac != '' GROUP BY ip""", {"ips": ips})}
    for r in _q(db, f"""
        SELECT ip, mac_addr FROM (
            SELECT {ip_expr('src_ip')} AS ip, argMax(src_mac, timestamp) AS mac_addr FROM {db.name}.flow_records
            WHERE timestamp > now() - INTERVAL 1 DAY AND src_mac IS NOT NULL AND src_ip IS NOT NULL
              AND {ip_expr('src_ip')} IN {{ips:Array(String)}} GROUP BY ip)
        WHERE mac_addr NOT IN (
            SELECT src_mac FROM {db.name}.flow_records
            WHERE timestamp > now() - INTERVAL 1 DAY AND src_mac IS NOT NULL
            GROUP BY src_mac HAVING uniqExact(src_ip) > {GATEWAY_MAC_MIN_IPS})""", {"ips": ips}):
        macs.setdefault(r["ip"], r["mac_addr"])
    return {ip: (m or "").lower() for ip, m in macs.items() if m}


def resolve(db: Database, ips: Iterable[str]) -> dict[str, dict[str, str]]:
    """{ip: {"name": ..., "source": alias|mac-alias|controller|dns|subnet}} for the given IPs."""
    from . import inventory

    wanted = sorted({ip for ip in ips if ip})
    if not wanted:
        return {}
    out: dict[str, dict[str, str]] = {}

    def put(ip: str, name: str, source: str) -> None:
        if name and ip not in out:
            out[ip] = {"name": name, "source": source}

    aliases = _q(db, f"SELECT kind, key, name FROM {db.name}.host_aliases FINAL WHERE deleted = 0")
    by_ip = {a["key"]: a["name"] for a in aliases if a["kind"] == "ip"}
    by_mac = {a["key"]: a["name"] for a in aliases if a["kind"] == "mac"}
    subnets = []
    for a in aliases:
        if a["kind"] == "cidr":
            try:
                subnets.append((ipaddress.ip_network(a["key"]), a["name"]))
            except ValueError:
                pass
    subnets.sort(key=lambda s: -s[0].prefixlen)  # most specific first

    for ip in wanted:
        put(ip, by_ip.get(ip, ""), "alias")

    if by_mac:
        for ip, mac in ip_macs(db, wanted).items():
            put(ip, by_mac.get(mac, ""), "mac-alias")

    for ip, name in inventory.host_names(db).items():
        if ip in wanted:
            put(ip, name, "controller")

    for r in _q(db, f"""SELECT ip, argMax(name, resolved_at) AS ptr FROM {db.name}.dns_cache FINAL
                        WHERE ip IN {{ips:Array(String)}} AND status = 'ok' GROUP BY ip""", {"ips": wanted}):
        put(r["ip"], r["ptr"], "dns")

    if subnets:
        for ip in wanted:
            if ip in out:
                continue
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            for net, name in subnets:
                if addr.version == net.version and addr in net:
                    put(ip, name, "subnet")
                    break
    return out


def names_for(db: Database, ips: Iterable[str]) -> dict[str, str]:
    return {ip: v["name"] for ip, v in resolve(db, ips).items()}


def details_for(db: Database, ips: Iterable[str]) -> dict[str, dict[str, str | None]]:
    """{ip: {"name", "source", "mac", "vendor"}} (keys present only when known)."""
    wanted = sorted({ip for ip in ips if ip})
    named = resolve(db, wanted)
    macs = ip_macs(db, wanted)
    out: dict[str, dict[str, str | None]] = {}
    for ip in wanted:
        d: dict[str, str | None] = {}
        if ip in named:
            d.update(name=named[ip]["name"], source=named[ip]["source"])
        if ip in macs:
            d.update(mac=macs[ip], vendor=oui.vendor(macs[ip]))
        if d:
            out[ip] = d
    return out


def search(db: Database, q: str, limit: int = 50) -> list[dict[str, str]]:
    """Hosts whose name contains `q` (aliases, controller clients, DNS cache)."""
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return []
    params = {"q": needle, "limit": limit}
    found: dict[str, dict[str, str]] = {}
    for r in _q(db, f"""SELECT key AS ip, name, 'alias' AS source FROM {db.name}.host_aliases FINAL
                        WHERE deleted = 0 AND kind = 'ip' AND positionCaseInsensitive(name, {{q:String}}) > 0
                        LIMIT {{limit:UInt32}}""", params):
        found.setdefault(r["ip"], r)
    from .inventory import _source

    src = _source()  # only the controller currently selected
    if src != "none":
        for r in _q(db, f"""SELECT ip, argMax(name, synced_at) AS host FROM {db.name}.ctrl_clients FINAL
                            WHERE ip != '' AND source = {{src:String}} AND positionCaseInsensitive(name, {{q:String}}) > 0
                            GROUP BY ip LIMIT {{limit:UInt32}}""", {**params, "src": src}):
            found.setdefault(r["ip"], {"ip": r["ip"], "name": r["host"], "source": "controller"})
    for r in _q(db, f"""SELECT ip, argMax(name, resolved_at) AS host, 'dns' AS source FROM {db.name}.dns_cache FINAL
                        WHERE status = 'ok' AND positionCaseInsensitive(name, {{q:String}}) > 0 GROUP BY ip
                        LIMIT {{limit:UInt32}}""", params):
        found.setdefault(r["ip"], {"ip": r["ip"], "name": r["host"], "source": "dns"})
    return list(found.values())[:limit]
