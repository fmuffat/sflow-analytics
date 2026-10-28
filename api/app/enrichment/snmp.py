"""SNMP polling of switches that no controller (RUCKUS One / SmartZone) knows.

Standard MIBs only (supported by FastIron, see the FastIron MIB reference "Supported standard
MIBs"): SNMPv2-MIB (sysName, sysDescr), IF-MIB ifXTable (ifName, ifAlias), ENTITY-MIB
(serial number) and LLDP-MIB (neighbors). SNMP v2c or v3 (authPriv / authNoPriv).

Each switch that sends sFlow (agent IP) and is not known by the selected controller is polled
every `interval_minutes`; the results go into the controller tables with source = 'snmp', so
that names, descriptions and LLDP neighbors are shown like with a controller. Controller data
win when both exist. Read-only: GET / GETBULK only.

Settings (configuration store, secrets encrypted):
    snmp.enabled, snmp.version (v2c | v3), snmp.port, snmp.timeout, snmp.interval_minutes
    snmp.community (secret)
    snmp.v3.username, snmp.v3.auth_protocol, snmp.v3.auth_key (secret),
    snmp.v3.priv_protocol, snmp.v3.priv_key (secret)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .. import security
from ..db import Database
from . import icx

SOURCE = "snmp"
VERSIONS = ("v2c", "v3")
AUTH_PROTOCOLS = ("SHA", "SHA-256", "SHA-512", "MD5")
PRIV_PROTOCOLS = ("AES", "AES-256", "DES", "none")
DEFAULT_INTERVAL = 15
CONCURRENCY = 8

SYS_DESCR, SYS_NAME = "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.5.0"
IF_NAME, IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.1", "1.3.6.1.2.1.31.1.1.1.18"
IF_HIGH_SPEED, IF_OPER = "1.3.6.1.2.1.31.1.1.1.15", "1.3.6.1.2.1.2.2.1.8"
ENT_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11"
LLDP_REM = "1.0.8802.1.1.2.1.4.1.1"   # lldpRemEntry: .4 chassis subtype, .5 chassis id, .8 port desc, .9 sys name
TABLES = (IF_NAME, IF_ALIAS, IF_HIGH_SPEED, IF_OPER, ENT_SERIAL, LLDP_REM)


class SnmpError(Exception):
    pass


# --- settings ------------------------------------------------------------------------------

def get_config(include_secret: bool = False) -> dict[str, Any]:
    g = security.get_setting
    cfg: dict[str, Any] = {
        "enabled": g("snmp.enabled", "false") == "true",
        "version": g("snmp.version", "v2c"),
        "port": int(g("snmp.port", "161") or 161),
        "timeout": float(g("snmp.timeout", "2") or 2),
        "interval_minutes": int(g("snmp.interval_minutes", str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL),
        "community_set": bool(g("snmp.community", "")),
        "v3": {
            "username": g("snmp.v3.username", ""),
            "auth_protocol": g("snmp.v3.auth_protocol", "SHA"),
            "priv_protocol": g("snmp.v3.priv_protocol", "AES"),
            "auth_key_set": bool(g("snmp.v3.auth_key", "")),
            "priv_key_set": bool(g("snmp.v3.priv_key", "")),
        },
    }
    if include_secret:
        cfg["community"] = g("snmp.community", "")
        cfg["v3"]["auth_key"] = g("snmp.v3.auth_key", "")
        cfg["v3"]["priv_key"] = g("snmp.v3.priv_key", "")
    return cfg


def save_config(body: dict[str, Any]) -> None:
    s = security.set_setting
    if body.get("version") is not None and body["version"] not in VERSIONS:
        raise SnmpError("version must be v2c or v3")
    for k in ("enabled", "version", "port", "timeout", "interval_minutes"):
        if body.get(k) is not None:
            v = body[k]
            s(f"snmp.{k}", ("true" if v else "false") if isinstance(v, bool) else str(v))
    if body.get("community"):
        s("snmp.community", body["community"], secret=True)
    v3 = body.get("v3") or {}
    if v3.get("auth_protocol") is not None and v3["auth_protocol"] not in AUTH_PROTOCOLS:
        raise SnmpError(f"auth protocol: one of {', '.join(AUTH_PROTOCOLS)}")
    if v3.get("priv_protocol") is not None and v3["priv_protocol"] not in PRIV_PROTOCOLS:
        raise SnmpError(f"privacy protocol: one of {', '.join(PRIV_PROTOCOLS)}")
    for k in ("username", "auth_protocol", "priv_protocol"):
        if v3.get(k) is not None:
            s(f"snmp.v3.{k}", str(v3[k]))
    for k in ("auth_key", "priv_key"):
        if v3.get(k):
            if len(v3[k]) < 8:
                raise SnmpError("SNMPv3 keys are at least 8 characters")
            s(f"snmp.v3.{k}", v3[k], secret=True)
    cfg = get_config(include_secret=True)
    if cfg["enabled"]:
        if cfg["version"] == "v2c" and not cfg["community"]:
            raise SnmpError("SNMP v2c needs a community")
        if cfg["version"] == "v3" and not (cfg["v3"]["username"] and cfg["v3"]["auth_key"]):
            raise SnmpError("SNMPv3 needs a user name and an authentication key")


# --- protocol (pysnmp) -----------------------------------------------------------------------

def _auth(cfg: dict[str, Any]):
    from pysnmp.hlapi.v3arch.asyncio import (CommunityData, UsmUserData, usmAesCfb128Protocol,
                                             usmAesCfb256Protocol, usmDESPrivProtocol, usmHMAC384SHA512AuthProtocol,
                                             usmHMAC192SHA256AuthProtocol, usmHMACMD5AuthProtocol,
                                             usmHMACSHAAuthProtocol, usmNoPrivProtocol)

    if cfg["version"] == "v2c":
        return CommunityData(cfg["community"], mpModel=1)
    v3 = cfg["v3"]
    auth = {"SHA": usmHMACSHAAuthProtocol, "SHA-256": usmHMAC192SHA256AuthProtocol,
            "SHA-512": usmHMAC384SHA512AuthProtocol, "MD5": usmHMACMD5AuthProtocol}[v3["auth_protocol"]]
    priv = {"AES": usmAesCfb128Protocol, "AES-256": usmAesCfb256Protocol, "DES": usmDESPrivProtocol,
            "none": usmNoPrivProtocol}[v3["priv_protocol"]]
    if v3["priv_protocol"] == "none":
        return UsmUserData(v3["username"], authKey=v3["auth_key"], authProtocol=auth, privProtocol=priv)
    return UsmUserData(v3["username"], authKey=v3["auth_key"], privKey=v3["priv_key"], authProtocol=auth, privProtocol=priv)


def _value(v: Any) -> Any:
    """pysnmp value -> int, or bytes for strings (decoded by the parser)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        pass
    try:
        return bytes(v)
    except (TypeError, ValueError):
        return str(v)


async def _fetch_one(cfg: dict[str, Any], ip: str, tables: tuple[str, ...]) -> dict[str, Any]:
    from pysnmp.hlapi.v3arch.asyncio import (ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget,
                                             bulk_walk_cmd, get_cmd)

    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create((ip, cfg["port"]), timeout=cfg["timeout"], retries=1)
        auth = _auth(cfg)
        err, status, _, binds = await get_cmd(engine, auth, target, ContextData(),
                                              ObjectType(ObjectIdentity(SYS_NAME)), ObjectType(ObjectIdentity(SYS_DESCR)))
        if err or status:
            raise SnmpError(str(err or status.prettyPrint()))
        out: dict[str, Any] = {SYS_NAME: _value(binds[0][1]), SYS_DESCR: _value(binds[1][1])}
        for base in tables:
            rows: dict[str, Any] = {}
            async for err, status, _, vbs in bulk_walk_cmd(engine, auth, target, ContextData(), 0, 25,
                                                           ObjectType(ObjectIdentity(base)), lexicographicMode=False):
                if err or status:   # a table the switch does not have: skipped
                    break
                for name, val in vbs:
                    oid = str(name)
                    if oid.startswith(base + "."):
                        rows[oid[len(base) + 1:]] = _value(val)
            out[base] = rows
        return out
    finally:
        engine.close_dispatcher()


def fetch(cfg: dict[str, Any], ips: list[str], tables: tuple[str, ...] = TABLES) -> dict[str, Any]:
    """{ip: raw data | SnmpError}, CONCURRENCY switches at a time."""
    async def run() -> dict[str, Any]:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(ip: str):
            async with sem:
                try:
                    return ip, await _fetch_one(cfg, ip, tables)
                except Exception as exc:  # noqa: BLE001 - recorded per switch
                    return ip, SnmpError(f"{type(exc).__name__}: {exc}" if not isinstance(exc, SnmpError) else str(exc))
        return dict(await asyncio.gather(*(one(ip) for ip in ips)))
    return asyncio.run(run())


# --- parsing -------------------------------------------------------------------------------

def _text(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").strip("\x00 \r\n\t")
    return "" if v is None else str(v).strip()


def _mac(v: Any) -> str:
    return ":".join(f"{b:02x}" for b in v) if isinstance(v, bytes) and len(v) == 6 else _text(v).lower()


_PORT = re.compile(r"(\d+/\d+/\d+)$")
_LAG = re.compile(r"^(?:lg|lag|port-channel|trunk)\s*(\d+)$", re.IGNORECASE)


def port_id(if_name: str) -> str:
    """'GigabitEthernet1/1/8' or '1/1/8' -> '1/1/8'; 'lg3' -> 'LAG3'; anything else as is."""
    m = _PORT.search(if_name)
    if m:
        return m.group(1)
    m = _LAG.match(if_name.strip())
    return f"LAG{m.group(1)}" if m else if_name.strip()


def parse(ip: str, raw: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Raw SNMP data of one switch -> ctrl_switches row and ctrl_ports rows."""
    descr = _text(raw.get(SYS_DESCR))
    serials = sorted((raw.get(ENT_SERIAL) or {}).items(), key=lambda kv: [int(x) for x in kv[0].split(".") if x.isdigit()])
    serial = next((s for s in (_text(v) for _, v in serials) if s), "")
    model = re.search(r"\b(ICX[\w-]+)", descr)
    firmware = re.search(r"[Vv]ersion\s+([\w.]+)", descr)
    switch = {"serial": serial or f"ip:{ip}", "name": _text(raw.get(SYS_NAME)), "model": model.group(1) if model else "",
              "firmware": firmware.group(1) if firmware else "", "ip": ip, "mac": "", "venue": "", "status": "reachable"}
    names, aliases = raw.get(IF_NAME) or {}, raw.get(IF_ALIAS) or {}
    speeds, oper = raw.get(IF_HIGH_SPEED) or {}, raw.get(IF_OPER) or {}
    # LLDP remote table index: timeMark.localPortNum.index (localPortNum = ifIndex on FastIron)
    lldp: dict[int, dict[str, Any]] = {}
    for suffix, v in (raw.get(LLDP_REM) or {}).items():
        parts = suffix.split(".")
        if len(parts) < 4 or not parts[2].isdigit():
            continue
        col, local = parts[0], int(parts[2])
        e = lldp.setdefault(local, {})
        if col in ("4", "5", "8", "9"):
            e[col] = v
    ports = []
    for idx, raw_name in names.items():
        if not idx.isdigit():
            continue
        i = int(idx)
        n = lldp.get(i, {})
        chassis = _mac(n.get("5")) if n.get("4") == 4 else ""
        ports.append({
            "switch_serial": switch["serial"], "port_id": port_id(_text(raw_name)) or f"ifIndex {i}", "ifindex": i,
            "name": icx.user_port_name(_text(aliases.get(idx))),
            "status": {1: "Up", 2: "Down"}.get(oper.get(idx), ""), "admin_status": "",
            "speed": "", "vlan_untagged": "", "vlans": "", "lag_id": "", "lag_name": "",
            "lldp_name": _text(n.get("9")) or ("" if chassis else _text(n.get("5"))), "lldp_mac": chassis, "lldp_port_mac": "",
        })
    return switch, ports


# --- poll ----------------------------------------------------------------------------------

def targets(db: Database) -> list[str]:
    """Agent IPs of the switches that send sFlow and that the selected controller does not know."""
    from .. import inventory

    known = set(inventory.controller_switches(db, include_snmp=False))
    return sorted({e["agent_ip"] for e in inventory.exporters(db) if e["agent_ip"] and e["agent_ip"] not in known})


def test(db: Database, ip: str | None = None, fetcher: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    cfg = get_config(include_secret=True)
    ips = [ip] if ip else targets(db)[:1]
    if not ips:
        raise SnmpError("no switch to test: every switch is known by the controller, or none sends sFlow yet")
    started = time.time()
    res = (fetcher or fetch)(cfg, ips, (IF_NAME,))[ips[0]]
    if isinstance(res, Exception):
        raise SnmpError(f"{ips[0]}: {res or 'no answer (check the community / SNMPv3 user and the switch ACL)'}")
    sw, ports = parse(ips[0], res)
    return {"ip": ips[0], "name": sw["name"], "model": sw["model"], "firmware": sw["firmware"], "interfaces": len(ports),
            "duration_ms": int((time.time() - started) * 1000)}


def last_poll() -> dict[str, Any] | None:
    raw = security.get_setting("snmp.last_poll")
    return json.loads(raw) if raw else None


def due(now: float | None = None) -> bool:
    cfg = get_config()
    if not cfg["enabled"]:
        return False
    last = last_poll()
    if not last:
        return True
    started = datetime.fromisoformat(last["started_at"]).timestamp()
    return (now or time.time()) - started >= cfg["interval_minutes"] * 60


def run_poll(db: Database, fetcher: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Polls every target and writes the results. Never raises; recorded in settings."""
    from .sync import PORT_COLS, SWITCH_COLS, _rows

    started = time.time()
    result: dict[str, Any] = {"started_at": security.now_iso(), "ok": True}
    try:
        cfg = get_config(include_secret=True)
        if not cfg["enabled"]:
            result.update(skipped=True)
            return result
        ips = targets(db)
        raw = (fetcher or fetch)(cfg, ips) if ips else {}
        switches, ports, errors = [], [], {}
        for ip in ips:
            r = raw.get(ip)
            if isinstance(r, Exception) or r is None:
                errors[ip] = str(r) or "no answer"
                continue
            sw, ps = parse(ip, r)
            switches.append(sw)
            ports += ps
        now = datetime.now(timezone.utc)
        if switches:
            db.insert("ctrl_switches", _rows(SOURCE, switches, SWITCH_COLS, now), SWITCH_COLS)
        if ports:
            db.insert("ctrl_ports", _rows(SOURCE, ports, PORT_COLS, now), PORT_COLS)
        result.update(switches=len(switches), ports=len(ports), targets=len(ips), errors=errors,
                      lldp_neighbors=sum(1 for p in ports if p["lldp_name"] or p["lldp_mac"]),
                      ok=not errors or bool(switches))
        return result
    except Exception as exc:  # noqa: BLE001 - recorded, the job reports it
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        result["duration_ms"] = int((time.time() - started) * 1000)
        security.set_setting("snmp.last_poll", json.dumps(result))
