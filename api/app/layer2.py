"""Layer-2 views: top MAC addresses and EtherTypes (sampled flows, estimates)."""

from __future__ import annotations

from typing import Any

from . import names, oui
from .catalog import ip_expr
from .db import Database
from .filters import TrafficFilter
from .traffic import _finish, _top, envelope, totals

ETHERTYPES = {
    0x0800: "IPv4", 0x86DD: "IPv6", 0x0806: "ARP", 0x8035: "RARP", 0x88CC: "LLDP", 0x8809: "LACP / Slow protocols",
    0x888E: "802.1X (EAPOL)", 0x88A8: "802.1ad (QinQ)", 0x8100: "802.1Q", 0x8847: "MPLS", 0x8848: "MPLS multicast",
    0x8863: "PPPoE discovery", 0x8864: "PPPoE session", 0x88F7: "PTP", 0x8906: "FCoE", 0x8914: "FIP",
    0x893A: "IEEE 1905.1", 0x88E5: "MACsec", 0x9000: "Loopback (CTP)", 0x22F3: "TRILL", 0x88B5: "Local experimental",
    0x8899: "Realtek RRCP / RLDP", 0x887B: "HomePlug 1.0 (powerline)", 0x88E1: "HomePlug AV (powerline)",
    0x88D9: "LLTD (Microsoft)", 0x88B7: "OUI extended", 0x8892: "PROFINET", 0x88A4: "EtherCAT", 0x88BA: "IEC 61850 SV",
    0x88B8: "IEC 61850 GOOSE", 0x88F8: "NC-SI", 0x8808: "Ethernet flow control (PAUSE)", 0x9100: "802.1Q (QinQ, legacy)",
}


def ethertype_name(v: int | None) -> str:
    if v is None:
        return "802.3 / LLC (STP, CDP...)"
    return ETHERTYPES.get(int(v), f"0x{int(v):04X}")


def top_macs(db: Database, f: TrafficFilter, direction: str, limit: int) -> dict[str, Any]:
    mac, ip = ("src_mac", "src_ip") if direction == "src" else ("dst_mac", "dst_ip")
    total = totals(db, f)
    rows = _top(db, f, (f"{mac} AS mac, uniqExact({ip}) AS ips, argMax({ip_expr(ip)}, timestamp) AS last_ip, "
                        "groupUniqArray(10)(vlan) AS vlans"), mac, limit, f"{mac} IS NOT NULL")
    aliases = {a["key"]: a["name"] for a in names.list_aliases(db) if a["kind"] == "mac"} if rows else {}
    ip_names = names.names_for(db, [r["last_ip"] for r in rows if r["last_ip"] and r["ips"] == 1])
    for r in rows:
        r["ips"] = int(r["ips"])
        r["vendor"] = oui.vendor(r["mac"])
        r["gateway"] = r["ips"] > names.GATEWAY_MAC_MIN_IPS
        # The IP (and its name) only identify the device when the MAC carries a single address.
        r["ip"] = r.pop("last_ip") if r["ips"] == 1 else None
        r["name"] = aliases.get(r["mac"]) or (ip_names.get(r["ip"]) if r["ip"] else None)
        r["vlans"] = sorted(v for v in r["vlans"] if v is not None)
    return envelope(f, _finish(rows, total), total, direction=direction)


def top_ethertypes(db: Database, f: TrafficFilter, limit: int) -> dict[str, Any]:
    total = totals(db, f)
    rows = _top(db, f, "ether_type", "ether_type", limit)
    for r in rows:
        r["name"] = ethertype_name(r["ether_type"])
        r["ether_type_hex"] = None if r["ether_type"] is None else f"0x{int(r['ether_type']):04X}"
    return envelope(f, _finish(rows, total), total)
