"""Named groups of IPs/subnets and of interfaces.

IP groups are expanded into address ranges, interface groups into
(exporter, ifIndex) pairs; both become parameterized SQL, either as filters
or as a classifier expression (to group traffic by group). An IP belonging to
several groups is classified in the first one by name order.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from typing import Any

from .db import Database

KINDS = ("ip", "if")
UNGROUPED = "Ungrouped"
_IF_MEMBER = re.compile(r"^([0-9A-Fa-f:.]+/[0-9A-Fa-f:.]+/\d+)/(\d+)$")
_EXP = re.compile(r"^([0-9A-Fa-f:.]+)/([0-9A-Fa-f:.]+)/(\d+)$")


class GroupError(ValueError):
    pass


def normalize_member(kind: str, m: str) -> str:
    v = (m or "").strip()
    if kind == "ip":
        try:
            net = ipaddress.ip_network(v, strict=False)
        except ValueError as exc:
            raise GroupError(f"invalid IP or subnet: {m!r}") from exc
        return str(net.network_address) if net.num_addresses == 1 else str(net)
    if kind == "if":
        mm = _IF_MEMBER.match(v)
        if not mm:
            raise GroupError(f"invalid interface id {m!r} (expected exporter_ip/agent_ip/sub_id/ifindex)")
        return v
    raise GroupError("kind must be ip or if")


def normalize(kind: str, name: str, members: list[str]) -> tuple[str, list[str]]:
    if kind not in KINDS:
        raise GroupError("kind must be ip or if")
    n = (name or "").strip()
    if not n or len(n) > 48 or n == UNGROUPED:
        raise GroupError("name must be 1-48 characters (and not 'Ungrouped')")
    if not members or len(members) > 500:
        raise GroupError("a group needs 1-500 members")
    return n, sorted({normalize_member(kind, m) for m in members})


def list_groups(db: Database, kind: str | None = None) -> list[dict[str, Any]]:
    where, params = "deleted = 0", {}
    if kind:
        where += " AND kind = {kind:String}"
        params["kind"] = kind
    try:
        return db.query(f"""SELECT kind, name, members, notes, updated_at FROM {db.name}.host_groups FINAL
                            WHERE {where} ORDER BY kind, name""", params)
    except Exception:  # noqa: BLE001 - table not created yet (collector not upgraded)
        return []


def save_group(db: Database, kind: str, name: str, members: list[str], notes: str = "") -> dict[str, Any]:
    n, ms = normalize(kind, name, members)
    db.insert("host_groups", [[kind, n, ms, (notes or "")[:500], 0, datetime.now(timezone.utc)]],
              ["kind", "name", "members", "notes", "deleted", "updated_at"])
    return {"kind": kind, "name": n, "members": ms, "notes": notes}


def delete_group(db: Database, kind: str, name: str) -> None:
    db.insert("host_groups", [[kind, name, [], "", 1, datetime.now(timezone.utc)]],
              ["kind", "name", "members", "notes", "deleted", "updated_at"])


# --- SQL builders -----------------------------------------------------------------

def ip_ranges(members: list[str]) -> list[tuple[str, str]]:
    from .filters import ip_range

    return [ip_range(m) for m in members]


class Sql:
    """Collects parameters for generated SQL fragments."""

    def __init__(self, prefix: str, params: dict[str, Any]):
        self.prefix, self.params, self.n = prefix, params, 0

    def p(self, value: Any, ch_type: str) -> str:
        self.n += 1
        name = f"{self.prefix}{self.n}"
        self.params[name] = value
        return f"{{{name}:{ch_type}}}"


def ip_group_condition(col: str, members: list[str], sql: Sql) -> str:
    parts = [f"({col} BETWEEN {sql.p(lo, 'IPv6')} AND {sql.p(hi, 'IPv6')})" for lo, hi in ip_ranges(members)]
    return "(" + " OR ".join(parts) + ")" if parts else "0"


def if_group_condition(members: list[str], sql: Sql, direction: str = "either") -> str:
    """Traffic entering/leaving (or either) an interface of the group."""
    parts = []
    for m in members:
        mm = _IF_MEMBER.match(m)
        if not mm:
            continue
        exp = _EXP.match(mm.group(1))
        if not exp:
            continue
        from .filters import ip_range

        e_ip, a_ip, sub = ip_range(exp.group(1))[0], ip_range(exp.group(2))[0], int(exp.group(3))
        idx = sql.p(int(mm.group(2)), "UInt32")
        cols = {"in": [f"input_ifindex = {idx}"], "out": [f"output_ifindex = {idx}"],
                "either": [f"input_ifindex = {idx}", f"output_ifindex = {idx}"]}[direction]
        parts.append(f"(exporter_ip = {sql.p(e_ip, 'IPv6')} AND agent_ip = {sql.p(a_ip, 'IPv6')} "
                     f"AND agent_sub_id = {sql.p(sub, 'UInt32')} AND ({' OR '.join(cols)}))")
    return "(" + " OR ".join(parts) + ")" if parts else "0"


def ip_classifier(col: str, groups: list[dict[str, Any]], sql: Sql) -> str:
    """multiIf(...) naming the group of an address column ('Ungrouped', 'Non-IP')."""
    branches = [f"isNull({col}), 'Non-IP'"]
    for g in groups:
        branches.append(f"{ip_group_condition(col, g['members'], sql)}, {sql.p(g['name'], 'String')}")
    return f"multiIf({', '.join(branches)}, '{UNGROUPED}')"


def members_of(db: Database, kind: str, names: list[str]) -> dict[str, list[str]]:
    wanted = set(names)
    return {g["name"]: list(g["members"]) for g in list_groups(db, kind) if g["name"] in wanted}


# --- analytics ------------------------------------------------------------------------

def top_groups(db: Database, f, direction: str, limit: int) -> dict[str, Any]:
    """Traffic per IP group of the source (or destination) address."""
    from .traffic import _finish, _with, envelope, totals

    col = "src_ip" if direction == "src" else "dst_ip"
    groups = list_groups(db, "ip")
    where, params = f.where()
    params["limit"] = limit
    cls = ip_classifier(col, groups, Sql("c", params))
    total = totals(db, f)
    rows = db.query(f"""{_with()}
        SELECT {cls} AS grp, sum(estimated_bytes) AS bytes, sum(estimated_packets) AS packets, count() AS samples
        FROM {db.name}.flow_records WHERE {where}
        GROUP BY grp ORDER BY bytes DESC LIMIT {{limit:UInt32}}""", params)
    return envelope(f, _finish(rows, total), total, groups=len(groups))


def group_matrix(db: Database, f) -> dict[str, Any]:
    """Traffic between IP groups (source group x destination group)."""
    from .traffic import _finish, _with, envelope, totals

    groups = list_groups(db, "ip")
    where, params = f.where()
    sql = Sql("c", params)
    src_cls, dst_cls = ip_classifier("src_ip", groups, sql), ip_classifier("dst_ip", groups, sql)
    total = totals(db, f)
    rows = db.query(f"""{_with()}
        SELECT {src_cls} AS src_group, {dst_cls} AS dst_group,
               sum(estimated_bytes) AS bytes, sum(estimated_packets) AS packets, count() AS samples
        FROM {db.name}.flow_records WHERE {where}
        GROUP BY src_group, dst_group ORDER BY bytes DESC LIMIT 400""", params)
    return envelope(f, _finish(rows, total), total, groups=[g["name"] for g in groups] + [UNGROUPED])
