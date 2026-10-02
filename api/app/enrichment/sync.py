"""Controller synchronisation: settings, fetch, write to ClickHouse, status.

Settings (configuration store, secrets encrypted):
    enrichment.source                none | ruckusone | smartzone
    enrichment.interval_minutes      default 15
    enrichment.r1.region / tenant_id / client_id / client_secret (secret)
    enrichment.sz.host / port / username / password (secret) / verify_tls
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .. import security
from ..db import Database
from . import icx
from .ruckusone import R1Config, R1Error, RuckusOneClient
from .smartzone import SmartZoneClient, SZConfig, SZError, port_from_interface, port_label

CtrlError = (R1Error, SZError)

SOURCES = ("none", "ruckusone", "smartzone")
DEFAULT_INTERVAL = 15


class SyncError(Exception):
    pass


def get_config(include_secret: bool = False) -> dict[str, Any]:
    g = security.get_setting
    cfg: dict[str, Any] = {
        "source": g("enrichment.source", "none"),
        "interval_minutes": int(g("enrichment.interval_minutes", str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL),
        "ruckusone": {
            "region": g("enrichment.r1.region", "eu"),
            "tenant_id": g("enrichment.r1.tenant_id", ""),
            "client_id": g("enrichment.r1.client_id", ""),
            "client_secret_set": bool(g("enrichment.r1.client_secret", "")),
        },
    }
    cfg["smartzone"] = {
        "host": g("enrichment.sz.host", ""),
        "port": int(g("enrichment.sz.port", "8443") or 8443),
        "username": g("enrichment.sz.username", ""),
        "verify_tls": g("enrichment.sz.verify_tls", "true") == "true",
        "password_set": bool(g("enrichment.sz.password", "")),
    }
    if include_secret:
        cfg["ruckusone"]["client_secret"] = g("enrichment.r1.client_secret", "")
        cfg["smartzone"]["password"] = g("enrichment.sz.password", "")
    return cfg


def save_config(body: dict[str, Any]) -> None:
    src = body.get("source")
    if src is not None:
        if src not in SOURCES:
            raise SyncError(f"source must be one of {', '.join(SOURCES)}")
        security.set_setting("enrichment.source", src)
    if body.get("interval_minutes") is not None:
        n = int(body["interval_minutes"])
        if not 5 <= n <= 1440:
            raise SyncError("interval_minutes must be 5-1440")
        security.set_setting("enrichment.interval_minutes", str(n))
    r1 = body.get("ruckusone") or {}
    if r1.get("region") is not None:
        from .ruckusone import REGIONS
        if r1["region"] not in REGIONS:
            raise SyncError(f"region must be one of {', '.join(REGIONS)}")
        security.set_setting("enrichment.r1.region", r1["region"])
    for k in ("tenant_id", "client_id"):
        if r1.get(k) is not None:
            security.set_setting(f"enrichment.r1.{k}", str(r1[k]).strip())
    if r1.get("client_secret"):  # empty = keep the stored secret
        security.set_setting("enrichment.r1.client_secret", str(r1["client_secret"]).strip(), secret=True)
    sz = body.get("smartzone") or {}
    for k in ("host", "username"):
        if sz.get(k) is not None:
            security.set_setting(f"enrichment.sz.{k}", str(sz[k]).strip())
    if sz.get("port") is not None:
        security.set_setting("enrichment.sz.port", str(int(sz["port"])))
    if sz.get("verify_tls") is not None:
        security.set_setting("enrichment.sz.verify_tls", "true" if sz["verify_tls"] else "false")
    if sz.get("password"):  # empty = keep the stored secret
        security.set_setting("enrichment.sz.password", str(sz["password"]), secret=True)


def _r1_client(transport=None) -> RuckusOneClient:
    c = get_config(include_secret=True)["ruckusone"]
    return RuckusOneClient(R1Config(c["region"], c["tenant_id"], c["client_id"], c["client_secret"]), transport=transport)


def _sz_client(transport=None) -> SmartZoneClient:
    c = get_config(include_secret=True)["smartzone"]
    return SmartZoneClient(SZConfig(c["host"], c["port"], c["username"], c["password"], c["verify_tls"]),
                           transport=transport)


def _client(source: str, transport=None):
    if source == "ruckusone":
        return _r1_client(transport)
    if source == "smartzone":
        return _sz_client(transport)
    raise SyncError("no enrichment source selected")


def test_connection(transport=None) -> dict[str, Any]:
    """Authenticates and counts switches (no data written)."""
    source = get_config()["source"]
    try:
        client = _client(source, transport)
    except CtrlError as exc:
        raise SyncError(str(exc)) from exc
    try:
        started = time.time()
        switches = client.switches()
        out = {"ok": True, "switches": len(switches), "duration_ms": int((time.time() - started) * 1000)}
        if source == "smartzone":
            out["api_version"] = client.version
        return out
    except CtrlError as exc:
        raise SyncError(str(exc)) from exc
    finally:
        client.close()


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(str(x).strip() for x in v)
    return str(v).strip()


def normalize_switch(s: dict[str, Any]) -> dict[str, str]:
    return {
        "serial": _s(s.get("serialNumber")), "name": _s(s.get("name") or s.get("switchName")),
        "model": _s(s.get("model")), "firmware": _s(s.get("firmware") or s.get("firmwareVersion")),
        "ip": _s(s.get("ipAddress")), "mac": _s(s.get("switchMac")).lower(), "venue": _s(s.get("venueName")),
        "status": _s(s.get("deviceStatus")),
    }


def normalize_ports(ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lags: dict[tuple[str, str], str] = {}
    for p in ports:
        pid = _s(p.get("portIdentifier"))
        serial = _s(p.get("switchSerial"))
        if not pid or not serial:
            continue
        lag_id = _s(p.get("lagId"))
        if lag_id and lag_id not in ("0", "-1"):
            lags.setdefault((serial, lag_id), _s(p.get("lagName")))
        out.append({
            "switch_serial": serial, "port_id": pid, "ifindex": icx.port_ifindex(pid) or 0,
            "name": icx.user_port_name(_s(p.get("name"))), "status": _s(p.get("status")), "admin_status": _s(p.get("adminStatus")),
            "speed": _s(p.get("portSpeed")), "vlan_untagged": _s(p.get("unTaggedVlan")), "vlans": _s(p.get("vlanIds")),
            "lag_id": lag_id, "lag_name": _s(p.get("lagName")), "lldp_name": _s(p.get("neighborName")),
            "lldp_mac": _s(p.get("neighborMacAddress")).lower(), "lldp_port_mac": _s(p.get("neighborPortMacAddress")).lower(),
        })
    # LAG interfaces have their own ifIndex (3072 + id) in sFlow.
    for (serial, lag_id), lag_name in lags.items():
        out.append({"switch_serial": serial, "port_id": f"LAG{lag_id}", "ifindex": icx.lag_ifindex(lag_id) or 0,
                    "name": lag_name, "status": "", "admin_status": "", "speed": "", "vlan_untagged": "", "vlans": "",
                    "lag_id": lag_id, "lag_name": lag_name, "lldp_name": "", "lldp_mac": "", "lldp_port_mac": ""})
    return out


def normalize_client(c: dict[str, Any]) -> dict[str, str]:
    return {
        "mac": _s(c.get("clientMac")).lower(), "ip": _s(c.get("clientIpv4Addr")), "ipv6": _s(c.get("clientIpv6Addr")),
        "name": _s(c.get("alias") or c.get("clientName") or c.get("dhcpClientHostName")),
        "device_type": _s(c.get("dhcpClientDeviceTypeName") or c.get("clientType")),
        "vendor": _s(c.get("dhcpClientOsVendorName")), "switch_serial": _s(c.get("switchSerialNumber")),
        "port_id": _s(c.get("switchPort")), "vlan": _s(c.get("clientVlan")),
    }


SWITCH_COLS = ["source", "serial", "name", "model", "firmware", "ip", "mac", "venue", "status", "synced_at"]
PORT_COLS = ["source", "switch_serial", "port_id", "ifindex", "name", "status", "admin_status", "speed", "vlan_untagged",
             "vlans", "lag_id", "lag_name", "lldp_name", "lldp_mac", "lldp_port_mac", "synced_at"]
CLIENT_COLS = ["source", "mac", "ip", "ipv6", "name", "device_type", "vendor", "switch_serial", "port_id", "vlan", "synced_at"]


def normalize_sz(switches: list[dict[str, Any]], ports: list[dict[str, Any]],
                 lldp: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """SmartZone switches, ports and LLDP table -> common rows. Ports reference
    their switch by id (MAC); LLDP rows by switch id + local interface name."""
    sw_rows, serial_by_id = [], {}
    for s in switches:
        serial = _s(s.get("serialNumber"))
        if not serial:
            continue
        serial_by_id[_s(s.get("id") or s.get("macAddress")).lower()] = serial
        sw_rows.append({
            "serial": serial, "name": _s(s.get("switchName")), "model": _s(s.get("model")),
            "firmware": _s(s.get("firmwareVersion")), "ip": _s(s.get("ipAddress")),
            "mac": _s(s.get("macAddress") or s.get("id")).lower(), "venue": _s(s.get("groupName")),
            "status": _s(s.get("status")),
        })
    neighbors: dict[tuple[str, str], dict[str, Any]] = {}
    for n in lldp:
        pid = port_from_interface(n.get("localPort"))
        if pid:
            neighbors[(_s(n.get("switchId")).lower(), pid)] = n
    port_rows = []
    for p in ports:
        sid = _s(p.get("switchId") or p.get("id")).lower()
        serial, pid = serial_by_id.get(sid), _s(p.get("portIdentifier"))
        if not serial or not pid:
            continue
        n = neighbors.get((sid, pid), {})
        port_rows.append({
            "switch_serial": serial, "port_id": pid, "ifindex": icx.port_ifindex(pid) or 0,
            "name": icx.user_port_name(port_label(p.get("name"))), "status": _s(p.get("status")),
            "admin_status": _s(p.get("adminStatus")),
            "speed": _s(p.get("portSpeed")), "vlan_untagged": _s(p.get("unTaggedVlan")), "vlans": _s(p.get("vlans")),
            "lag_id": "", "lag_name": _s(p.get("lagName")),
            "lldp_name": _s(n.get("remoteDeviceName") or p.get("neighborName")),
            "lldp_mac": _s(n.get("remoteDeviceMac")).lower(), "lldp_port_mac": _s(n.get("remotePortMac")).lower(),
        })
    return sw_rows, port_rows


def _fetch(source: str, client, result: dict[str, Any]) -> tuple[list, list, list]:
    if source == "smartzone":
        raw_switches, raw_ports = client.switches(), client.ports()
        try:
            lldp = client.lldp()
        except SZError as exc:  # LLDP table is optional
            lldp, result["lldp_error"] = [], str(exc)
        switches, ports = normalize_sz(raw_switches, raw_ports, lldp)
        return switches, ports, []
    switches = [normalize_switch(s) for s in client.switches()]
    ports = normalize_ports(client.ports())
    try:
        clients = [c for c in (normalize_client(x) for x in client.clients()) if c["mac"]]
    except R1Error as exc:  # clients are optional
        clients, result["clients_error"] = [], str(exc)
    return switches, ports, clients


def _rows(source: str, items: list[dict[str, Any]], cols: list[str], now: datetime) -> list[list[Any]]:
    return [[source if c == "source" else now if c == "synced_at" else it[c] for c in cols] for it in items]


def run_sync(db: Database, transport=None, client_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Fetches the controller inventory and writes it. Records the result in settings."""
    cfg = get_config()
    source = cfg["source"]
    started = time.time()
    result: dict[str, Any] = {"source": source, "started_at": security.now_iso()}
    try:
        if source == "none":
            result.update(ok=True, skipped=True)
            return result
        try:
            client = client_factory() if client_factory else _client(source, transport)
        except CtrlError as exc:
            raise SyncError(str(exc)) from exc
        try:
            switches, ports, clients = _fetch(source, client, result)
        except CtrlError as exc:
            raise SyncError(str(exc)) from exc
        finally:
            client.close()
        now = datetime.now(timezone.utc)
        if switches:
            db.insert("ctrl_switches", _rows(source, switches, SWITCH_COLS, now), SWITCH_COLS)
        if ports:
            db.insert("ctrl_ports", _rows(source, ports, PORT_COLS, now), PORT_COLS)
        if clients:
            db.insert("ctrl_clients", _rows(source, clients, CLIENT_COLS, now), CLIENT_COLS)
        result.update(ok=True, switches=len(switches), ports=len(ports), clients=len(clients),
                      lldp_neighbors=sum(1 for p in ports if p["lldp_name"] or p["lldp_mac"]))
        return result
    except SyncError as exc:
        result.update(ok=False, error=str(exc))
        return result
    finally:
        result["duration_ms"] = int((time.time() - started) * 1000)
        security.set_setting("enrichment.last_sync", json.dumps(result))


def last_sync() -> dict[str, Any] | None:
    raw = security.get_setting("enrichment.last_sync")
    return json.loads(raw) if raw else None


def due(now: float | None = None) -> bool:
    """True when the configured interval elapsed since the last sync."""
    cfg = get_config()
    if cfg["source"] == "none":
        return False
    last = last_sync()
    if not last:
        return True
    try:
        t = datetime.fromisoformat(last["started_at"]).timestamp()
    except (KeyError, ValueError):
        return True
    # After a failure, retry sooner than the normal interval.
    wait = cfg["interval_minutes"] * 60 if last.get("ok") else min(300, cfg["interval_minutes"] * 60)
    return (now or time.time()) - t >= wait
