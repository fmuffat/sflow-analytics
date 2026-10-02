"""Protocol names and service identification (protocol + well-known port).

This is port-based service naming, not DPI: a flow is labelled with the
service of its destination port, else of its source port (response
direction). The table is code for now; it will become editable later.
"""

from __future__ import annotations

PROTOCOLS: dict[int, str] = {
    1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 41: "IPv6-in-IPv4", 47: "GRE",
    50: "ESP", 51: "AH", 58: "ICMPv6", 89: "OSPF", 103: "PIM", 112: "VRRP", 132: "SCTP",
}

# (protocol, port) -> service name
SERVICES: dict[tuple[int, int], str] = {}


def _add(proto: int | tuple[int, ...], ports: int | tuple[int, ...], name: str) -> None:
    for p in proto if isinstance(proto, tuple) else (proto,):
        for port in ports if isinstance(ports, tuple) else (ports,):
            SERVICES[(p, port)] = name


TCP, UDP, BOTH = 6, 17, (6, 17)
_add(TCP, 80, "HTTP")
_add(TCP, 443, "HTTPS")
_add(UDP, 443, "QUIC")
_add(TCP, (8080, 8000), "HTTP-Alt")
_add(TCP, 8443, "HTTPS-Alt")
_add(TCP, 22, "SSH")
_add(TCP, 23, "Telnet")
_add(TCP, 21, "FTP")
_add(UDP, 69, "TFTP")
_add(TCP, 25, "SMTP")
_add(TCP, 465, "SMTPS")
_add(TCP, 587, "SMTP Submission")
_add(TCP, 110, "POP3")
_add(TCP, 995, "POP3S")
_add(TCP, 143, "IMAP")
_add(TCP, 993, "IMAPS")
_add(BOTH, 53, "DNS")
_add(TCP, 853, "DNS over TLS")
_add(UDP, 5353, "mDNS")
_add(UDP, (67, 68), "DHCP")
_add(UDP, (546, 547), "DHCPv6")
_add(UDP, 123, "NTP")
_add(TCP, 445, "SMB")
_add(TCP, 139, "NetBIOS")
_add(UDP, (137, 138), "NetBIOS")
_add(BOTH, 3389, "RDP")
_add(BOTH, 88, "Kerberos")
_add(TCP, 389, "LDAP")
_add(TCP, 636, "LDAPS")
_add(UDP, (161, 162), "SNMP")
_add(UDP, 514, "Syslog")
_add(UDP, (1812, 1813), "RADIUS")
_add(UDP, 1900, "SSDP")
_add(BOTH, 5060, "SIP")
_add(TCP, 5061, "SIP-TLS")
_add(UDP, (500, 4500), "IPsec")
_add(UDP, 6343, "sFlow")
_add(TCP, 3306, "MySQL")
_add(TCP, 5432, "PostgreSQL")
_add(TCP, 1433, "MS SQL")
_add(TCP, (1883, 8883), "MQTT")
_add(TCP, (8008, 8009), "Google Cast")

SERVICE_NAMES: set[str] = set(SERVICES.values())
EPHEMERAL_START = 49152


def protocol_number(value: str) -> int:
    """Accepts a protocol name (tcp, UDP, icmp...) or number."""
    v = value.strip()
    if v.isdigit():
        n = int(v)
        if 0 <= n <= 255:
            return n
        raise ValueError(f"protocol number out of range: {v}")
    for n, name in PROTOCOLS.items():
        if name.lower() == v.lower():
            return n
    raise ValueError(f"unknown protocol: {v}")


def _array(values: list) -> str:
    return "[" + ",".join(values) + "]"


def _quote(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def protocol_expr(col: str = "ip_protocol") -> str:
    """SQL expression naming the IP protocol ('Non-IP' when absent)."""
    keys = _array([str(k) for k in PROTOCOLS])
    names = _array([_quote(v) for v in PROTOCOLS.values()])
    return (f"if(isNull({col}), 'Non-IP', transform(assumeNotNull({col}), {keys}, {names}, "
            f"concat('IP-', toString(assumeNotNull({col})))))")


def service_expr() -> str:
    """SQL expression computing the service of a flow record.

    Order: known (proto, dst_port), known (proto, src_port), protocol-only
    names (ICMP, GRE...), '<PROTO>/<lowest port>' when that port is not
    ephemeral, else 'Unknown'. Non-IP frames are 'Non-IP'.
    Only constants from this module are embedded in the SQL.
    """
    keys = _array([str(p * 65536 + port) for (p, port) in SERVICES])
    names = _array([_quote(v) for v in SERVICES.values()])
    proto = "toUInt32(assumeNotNull(ip_protocol))"
    by_dst = f"transform({proto} * 65536 + ifNull(dst_port, 0), {keys}, {names}, '')"
    by_src = f"transform({proto} * 65536 + ifNull(src_port, 0), {keys}, {names}, '')"
    low_port = "least(ifNull(src_port, 65535), ifNull(dst_port, 65535))"
    return (
        "multiIf("
        "isNull(ip_protocol), 'Non-IP', "
        f"{by_dst} != '', {by_dst}, "
        f"{by_src} != '', {by_src}, "
        f"ip_protocol NOT IN (6, 17, 132), {protocol_expr()}, "
        f"{low_port} < {EPHEMERAL_START}, concat({protocol_expr()}, '/', toString({low_port})), "
        "'Unknown')"
    )


def ip_expr(col: str) -> str:
    """IPv6 column -> display string (IPv4-mapped shown as dotted IPv4)."""
    return f"replaceRegexpOne(IPv6NumToString({col}), '^::ffff:', '')"


def exporter_id_expr() -> str:
    """Same identifier as the collector: exporter_ip/agent_ip/sub_agent_id."""
    return f"concat({ip_expr('exporter_ip')}, '/', {ip_expr('agent_ip')}, '/', toString(agent_sub_id))"
