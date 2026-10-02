"""Exporter and interface inventory, merged with user-defined names."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .catalog import ip_expr
from .db import Database
from .enrichment import icx


def _source() -> str:
    """Only the controller currently selected feeds the inventory (both may have synced once)."""
    from . import security

    try:
        return security.get_setting("enrichment.source", "none") or "none"
    except Exception:  # noqa: BLE001 - no configuration store: no enrichment
        return "none"


def _safe_src(db: Database, sql: str) -> list[dict[str, Any]]:
    """Controller rows of the selected source. The tables may not exist yet
    (collector not upgraded): no enrichment then."""
    src = _source()
    if src == "none":
        return []
    try:
        return db.query(sql, {"src": src})
    except Exception:  # noqa: BLE001
        return []


def controller_switches(db: Database) -> dict[str, dict[str, Any]]:
    """Latest controller switches by management IP."""
    rows = _safe_src(db, f"""SELECT source, serial, name, model, firmware, ip, mac, venue, status, synced_at
                          FROM {db.name}.ctrl_switches FINAL WHERE ip != '' AND source = {{src:String}}""")
    return {r["ip"]: r for r in rows}


def host_names(db: Database) -> dict[str, str]:
    """IP -> endpoint name known by the controller (latest client record with a name).
    Later: manual aliases (priority) and reverse DNS (fallback)."""
    # Alias must differ from the column name, or WHERE would refer to the aggregate.
    rows = _safe_src(db, f"""SELECT ip, argMax(name, synced_at) AS host_name FROM {db.name}.ctrl_clients FINAL
                          WHERE ip != '' AND name != '' AND source = {{src:String}} GROUP BY ip""")
    return {r["ip"]: r["host_name"] for r in rows}


def controller_ports(db: Database) -> dict[tuple[str, int], dict[str, Any]]:
    rows = _safe_src(db, f"""SELECT switch_serial, port_id, ifindex, name, status, admin_status, speed, vlan_untagged,
                                lag_name, lldp_name, lldp_mac, lldp_port_mac, synced_at
                         FROM {db.name}.ctrl_ports FINAL WHERE ifindex > 0 AND source = {{src:String}}""")
    return {(r["switch_serial"], r["ifindex"]): r for r in rows}


def exporters(db: Database) -> list[dict[str, Any]]:
    rows = db.query(f"""
        SELECT e.id AS id, {ip_expr('e.exporter_ip')} AS exporter_ip, {ip_expr('e.agent_ip')} AS agent_ip,
               e.agent_sub_id AS agent_sub_id, e.first_seen AS first_seen, e.last_seen AS last_seen,
               e.sample_rate AS sample_rate, s.display_name AS display_name, s.notes AS notes
        FROM {db.name}.exporters AS e FINAL
        LEFT JOIN (SELECT id, display_name, notes FROM {db.name}.exporter_settings FINAL) AS s ON s.id = e.id
        ORDER BY e.id
        SETTINGS join_use_nulls = 1""")
    ctrl = controller_switches(db)
    for r in rows:
        r["display_name"] = r["display_name"] or None
        r["notes"] = r["notes"] or None
        c = ctrl.get(r["agent_ip"]) or ctrl.get(r["exporter_ip"])
        r["controller"] = ({k: c[k] for k in ("source", "serial", "name", "model", "firmware", "mac", "venue", "status", "synced_at")}
                           if c else None)
        # Name priority: manual name > controller name > agent IP.
        r["name"] = r["display_name"] or (c["name"] if c and c["name"] else None) or r["agent_ip"]
    return rows


def exporter(db: Database, exporter_id: str) -> dict[str, Any] | None:
    return next((e for e in exporters(db) if e["id"] == exporter_id), None)


def save_exporter_settings(db: Database, exporter_id: str, display_name: str, notes: str) -> None:
    db.insert("exporter_settings", [[exporter_id, display_name, notes, datetime.now(timezone.utc)]],
              ["id", "display_name", "notes", "updated_at"])


def interfaces(db: Database, exporter_id: str | None = None) -> list[dict[str, Any]]:
    where, params = "", {}
    if exporter_id:
        where, params = "WHERE i.exporter_id = {exp:String}", {"exp": exporter_id}
    rows = db.query(f"""
        SELECT i.exporter_id AS exporter_id, i.ifindex AS ifindex, i.first_seen AS first_seen,
               i.last_seen AS last_seen, i.speed_bps AS speed_bps, i.oper_up AS oper_up,
               s.name AS name, s.description AS description
        FROM {db.name}.interfaces AS i FINAL
        LEFT JOIN (SELECT exporter_id, ifindex, name, description FROM {db.name}.interface_settings FINAL) AS s
            ON s.exporter_id = i.exporter_id AND s.ifindex = i.ifindex
        {where}
        ORDER BY i.exporter_id, i.ifindex
        SETTINGS join_use_nulls = 1""", params)
    serials = {e["id"]: (e["controller"] or {}).get("serial") for e in exporters(db)}
    ports = controller_ports(db) if any(serials.values()) else {}
    for r in rows:
        decorate_interface(r, ports.get((serials.get(r["exporter_id"]), r["ifindex"])))
    return rows


def decorate_interface(r: dict[str, Any], ctrl: dict[str, Any] | None = None) -> None:
    """Label priority: manual name > controller port (e.g. '1/1/8 Uplink') > 'ifIndex N'.

    The ifIndex<->port mapping is derived from the ICX numbering; when the
    controller and sFlow disagree on the port speed the mapping is flagged
    uncertain and not used for the label."""
    r["name"] = r.get("name") or None
    r["description"] = r.get("description") or None
    r["id"] = f"{r['exporter_id']}/{r['ifindex']}"
    r["speed_bps"] = r.get("speed_bps") or None
    r["controller_port"] = None
    auto = None
    if ctrl:
        c_speed = icx.speed_bps(ctrl.get("speed"))
        uncertain = bool(c_speed and r["speed_bps"] and abs(c_speed - r["speed_bps"]) > 0.01 * r["speed_bps"])
        r["controller_port"] = {
            "port_id": ctrl["port_id"], "name": ctrl["name"] or None, "status": ctrl["status"] or None,
            "admin_status": ctrl["admin_status"] or None, "speed": ctrl["speed"] or None,
            "vlan_untagged": ctrl["vlan_untagged"] or None, "lag_name": ctrl["lag_name"] or None,
            "lldp_neighbor": ctrl["lldp_name"] or None, "lldp_mac": ctrl["lldp_mac"] or None,
            "lldp_port_mac": ctrl["lldp_port_mac"] or None, "mapping_uncertain": uncertain,
        }
        if not uncertain:
            auto = ctrl["port_id"] + (f" {ctrl['name']}" if ctrl["name"] else "")
            if not r["description"] and ctrl["name"]:
                r["description"] = ctrl["name"]
    r["label"] = r["name"] or auto or f"ifIndex {r['ifindex']}"


def interface(db: Database, exporter_id: str, ifindex: int) -> dict[str, Any] | None:
    return next((i for i in interfaces(db, exporter_id) if i["ifindex"] == ifindex), None)


def save_interface_settings(db: Database, exporter_id: str, ifindex: int, name: str, description: str) -> None:
    db.insert("interface_settings",
              [[exporter_id, ifindex, name, description, datetime.now(timezone.utc)]],
              ["exporter_id", "ifindex", "name", "description", "updated_at"])


def name_maps(db: Database) -> tuple[dict[str, str], dict[tuple[str, int], dict[str, Any]]]:
    """Exporter display names and interface metadata, for labelling results."""
    exp = {e["id"]: e["name"] for e in exporters(db)}
    ifs = {(i["exporter_id"], i["ifindex"]): i for i in interfaces(db)}
    return exp, ifs
