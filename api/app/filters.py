"""Traffic filters shared by every analytics endpoint.

Query parameters are parsed and validated into a TrafficFilter, which renders
a parameterized WHERE clause. Every list parameter accepts comma-separated
values (OR within a parameter, AND between parameters).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Query

from . import catalog
from .db import Database, get_db

RANGES = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000, "90d": 7776000}
DEFAULT_RANGE = "1h"

_EXPORTER_ID = re.compile(r"^([0-9A-Fa-f:.]+)/([0-9A-Fa-f:.]+)/(\d+)$")


class FilterError(ValueError):
    pass


def _split(value: str | None) -> list[str]:
    if value is None:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _ints(name: str, value: str | None, lo: int, hi: int) -> list[int]:
    out = []
    for v in _split(value):
        if not v.isdigit() or not lo <= int(v) <= hi:
            raise FilterError(f"{name}: {v!r} is not an integer in {lo}-{hi}")
        out.append(int(v))
    return out


def ip_range(value: str) -> tuple[str, str]:
    """IP or CIDR -> (first, last) as IPv6 strings, IPv4 mapped to ::ffff:."""
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise FilterError(f"invalid IP or subnet: {value!r}") from exc
    lo, hi = net.network_address, net.broadcast_address
    if isinstance(net, ipaddress.IPv4Network):
        return f"::ffff:{lo}", f"::ffff:{hi}"
    return str(lo), str(hi)


def parse_time(value: str) -> datetime:
    """ISO 8601 (naive = UTC) or Unix seconds."""
    v = value.strip()
    try:
        if re.fullmatch(r"\d+(\.\d+)?", v):
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise FilterError(f"invalid time: {value!r} (use ISO 8601 or Unix seconds)") from exc
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def normalize_service(value: str) -> str:
    """Canonical spelling of a service as produced by catalog.service_expr():
    a named service, a protocol name (ICMP, GRE, IP-99), '<PROTO>/<port>',
    'Unknown' or 'Non-IP'."""
    names = catalog.SERVICE_NAMES | set(catalog.PROTOCOLS.values()) | {"Unknown", "Non-IP"}
    known = {n.lower(): n for n in names}
    v = value.strip()
    if v.lower() in known:
        return known[v.lower()]
    if m := re.fullmatch(r"IP-(\d{1,3})", v, re.IGNORECASE):
        return f"IP-{int(m.group(1))}"
    if m := re.fullmatch(r"([A-Za-z0-9-]+)/(\d{1,5})", v):
        proto = known.get(m.group(1).lower())
        if proto in catalog.PROTOCOLS.values() and int(m.group(2)) <= 65535:
            return f"{proto}/{int(m.group(2))}"
    raise FilterError(f"unknown service: {value!r}")


@dataclass
class TrafficFilter:
    start: datetime
    end: datetime
    exporters: list[str] = field(default_factory=list)
    src_ips: list[str] = field(default_factory=list)
    dst_ips: list[str] = field(default_factory=list)
    ips: list[str] = field(default_factory=list)
    protocols: list[int] = field(default_factory=list)
    src_ports: list[int] = field(default_factory=list)
    dst_ports: list[int] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    vlans: list[int] = field(default_factory=list)
    input_ifindexes: list[int] = field(default_factory=list)
    output_ifindexes: list[int] = field(default_factory=list)
    ifindexes: list[int] = field(default_factory=list)
    # Group filters (names) and their members, resolved from the database.
    src_groups: list[str] = field(default_factory=list)
    dst_groups: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    if_groups: list[str] = field(default_factory=list)
    group_members: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def describe(self) -> dict[str, Any]:
        """Normalized echo of the filter, returned with every response."""
        d: dict[str, Any] = {"from": self.start.isoformat(), "to": self.end.isoformat()}
        for k in ("exporters", "src_ips", "dst_ips", "ips", "protocols", "src_ports", "dst_ports", "ports",
                  "services", "vlans", "input_ifindexes", "output_ifindexes", "ifindexes",
                  "src_groups", "dst_groups", "groups", "if_groups"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d

    def where(self) -> tuple[str, dict[str, Any]]:
        """WHERE clause (without the keyword) and its parameters.

        Uses the aliases `service` defined by the caller's WITH clause."""
        params: dict[str, Any] = {"f_start": self.start, "f_end": self.end}
        clauses = ["timestamp >= {f_start:DateTime64(3, 'UTC')}", "timestamp < {f_end:DateTime64(3, 'UTC')}"]
        n = 0

        def p(value: Any) -> str:
            nonlocal n
            n += 1
            name = f"f{n}"
            params[name] = value
            return name

        def any_of(parts: list[str]) -> None:
            if parts:
                clauses.append("(" + " OR ".join(parts) + ")")

        exp_parts = []
        for e in self.exporters:
            m = _EXPORTER_ID.match(e)
            if m:
                exp_ip, agent_ip, sub = ip_range(m.group(1))[0], ip_range(m.group(2))[0], int(m.group(3))
                exp_parts.append(f"(exporter_ip = {{{p(exp_ip)}:IPv6}} AND agent_ip = {{{p(agent_ip)}:IPv6}} "
                                 f"AND agent_sub_id = {{{p(sub)}:UInt32}})")
            else:
                ip = ip_range(e)[0]
                name = p(ip)
                exp_parts.append(f"(agent_ip = {{{name}:IPv6}} OR exporter_ip = {{{name}:IPv6}})")
        any_of(exp_parts)

        def ip_parts(cols: list[str], values: list[str]) -> None:
            parts = []
            for v in values:
                lo, hi = ip_range(v)
                a, b = p(lo), p(hi)
                parts += [f"({c} BETWEEN {{{a}:IPv6}} AND {{{b}:IPv6}})" for c in cols]
            any_of(parts)

        ip_parts(["src_ip"], self.src_ips)
        ip_parts(["dst_ip"], self.dst_ips)
        ip_parts(["src_ip", "dst_ip"], self.ips)

        def in_list(cols: list[str], values: list, ch_type: str) -> None:
            if values:
                name = p(values)
                any_of([f"{c} IN {{{name}:Array({ch_type})}}" for c in cols])

        in_list(["ip_protocol"], self.protocols, "UInt8")
        in_list(["src_port"], self.src_ports, "UInt16")
        in_list(["dst_port"], self.dst_ports, "UInt16")
        in_list(["src_port", "dst_port"], self.ports, "UInt16")
        in_list(["service"], self.services, "String")
        in_list(["vlan"], self.vlans, "UInt16")
        in_list(["input_ifindex"], self.input_ifindexes, "UInt32")
        in_list(["output_ifindex"], self.output_ifindexes, "UInt32")
        in_list(["input_ifindex", "output_ifindex"], self.ifindexes, "UInt32")

        if self.src_groups or self.dst_groups or self.groups or self.if_groups:
            from .groups import Sql, if_group_condition, ip_group_condition

            sql = Sql("g", params)
            members = lambda kind, name: self.group_members.get((kind, name), [])  # noqa: E731
            for cols, names in ((["src_ip"], self.src_groups), (["dst_ip"], self.dst_groups),
                                (["src_ip", "dst_ip"], self.groups)):
                if names:
                    any_of([ip_group_condition(c, members("ip", n), sql) for n in names for c in cols])
            if self.if_groups:
                any_of([if_group_condition(members("if", n), sql) for n in self.if_groups])
        return " AND ".join(clauses), params


def build_filter(
    from_: str | None, to: str | None, range_: str | None, *, now: datetime | None = None,
    max_days: int = 3650, **values: str | None,
) -> TrafficFilter:
    now = now or datetime.now(timezone.utc)
    end = parse_time(to) if to else now
    if from_:
        start = parse_time(from_)
    else:
        r = range_ or DEFAULT_RANGE
        if r not in RANGES:
            raise FilterError(f"range must be one of {', '.join(RANGES)}")
        start = end - timedelta(seconds=RANGES[r])
    if start >= end:
        raise FilterError("'from' must be before 'to'")
    if end - start > timedelta(days=max_days):
        raise FilterError(f"time range longer than {max_days} days")

    norm_services = [normalize_service(s) for s in _split(values.get("service"))]

    try:
        protocols = [catalog.protocol_number(v) for v in _split(values.get("protocol"))]
    except ValueError as exc:
        raise FilterError(str(exc)) from exc

    for key in ("src_ip", "dst_ip", "ip"):
        for v in _split(values.get(key)):
            ip_range(v)  # validate early for a clear error
    for e in _split(values.get("exporter")):
        m = _EXPORTER_ID.match(e)
        for part in (m.group(1), m.group(2)) if m else (e,):
            try:
                ipaddress.ip_address(part)
            except ValueError as exc:
                raise FilterError(f"exporter: {e!r} is not an IP or exporter id") from exc

    return TrafficFilter(
        start=start, end=end,
        exporters=_split(values.get("exporter")),
        src_ips=_split(values.get("src_ip")),
        dst_ips=_split(values.get("dst_ip")),
        ips=_split(values.get("ip")),
        protocols=protocols,
        src_ports=_ints("src_port", values.get("src_port"), 0, 65535),
        dst_ports=_ints("dst_port", values.get("dst_port"), 0, 65535),
        ports=_ints("port", values.get("port"), 0, 65535),
        services=norm_services,
        vlans=_ints("vlan", values.get("vlan"), 0, 4095),
        input_ifindexes=_ints("input_ifindex", values.get("input_ifindex"), 0, 2**32 - 1),
        output_ifindexes=_ints("output_ifindex", values.get("output_ifindex"), 0, 2**32 - 1),
        ifindexes=_ints("ifindex", values.get("ifindex"), 0, 2**32 - 1),
    )


def resolve_groups(f: TrafficFilter, db) -> TrafficFilter:
    """Loads the members of the groups named in the filter (FilterError if unknown)."""
    from .groups import members_of

    for kind, names in (("ip", f.src_groups + f.dst_groups + f.groups), ("if", f.if_groups)):
        if not names:
            continue
        found = members_of(db, kind, names)
        missing = sorted(set(names) - set(found))
        if missing:
            raise FilterError(f"unknown {'IP' if kind == 'ip' else 'interface'} group: {', '.join(missing)}")
        for n, ms in found.items():
            f.group_members[(kind, n)] = ms
    return f


def traffic_filter(
    from_: str | None = Query(None, alias="from", description="Start (ISO 8601 or Unix seconds). Default: `to` - range"),
    to: str | None = Query(None, description="End (ISO 8601 or Unix seconds). Default: now"),
    range_: str | None = Query(None, alias="range", description="Relative window when `from` is omitted: " + ", ".join(RANGES)),
    exporter: str | None = Query(None, description="Exporter id (exporter_ip/agent_ip/sub_id) or IP; comma-separated"),
    src_ip: str | None = Query(None, description="Source IP or CIDR; comma-separated"),
    dst_ip: str | None = Query(None, description="Destination IP or CIDR; comma-separated"),
    ip: str | None = Query(None, description="Source or destination IP or CIDR; comma-separated"),
    protocol: str | None = Query(None, description="Protocol name or number (tcp, udp, icmp, 47...); comma-separated"),
    src_port: str | None = Query(None, description="Source port; comma-separated"),
    dst_port: str | None = Query(None, description="Destination port; comma-separated"),
    port: str | None = Query(None, description="Source or destination port; comma-separated"),
    service: str | None = Query(None, description="Service name (HTTPS, DNS, TCP/8443, Unknown...); comma-separated"),
    vlan: str | None = Query(None, description="VLAN ID; comma-separated"),
    input_ifindex: str | None = Query(None, description="Ingress ifIndex; comma-separated"),
    output_ifindex: str | None = Query(None, description="Egress ifIndex; comma-separated"),
    ifindex: str | None = Query(None, description="Ingress or egress ifIndex; comma-separated"),
    src_group: str | None = Query(None, description="Source IP group name(s); comma-separated"),
    dst_group: str | None = Query(None, description="Destination IP group name(s); comma-separated"),
    group: str | None = Query(None, description="Source or destination IP group name(s); comma-separated"),
    if_group: str | None = Query(None, description="Interface group name(s) (ingress or egress); comma-separated"),
    db: Database = Depends(get_db),
) -> TrafficFilter:
    """FastAPI dependency: parses filters or answers 422."""
    from .config import get_settings

    try:
        f = build_filter(
            from_, to, range_, max_days=get_settings().retention_days + 1,
            exporter=exporter, src_ip=src_ip, dst_ip=dst_ip, ip=ip, protocol=protocol,
            src_port=src_port, dst_port=dst_port, port=port, service=service, vlan=vlan,
            input_ifindex=input_ifindex, output_ifindex=output_ifindex, ifindex=ifindex,
        )
        f.src_groups, f.dst_groups = _split(src_group), _split(dst_group)
        f.groups, f.if_groups = _split(group), _split(if_group)
        if f.src_groups or f.dst_groups or f.groups or f.if_groups:
            resolve_groups(f, db)
        return f
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
