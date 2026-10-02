"""ICX interface naming helpers.

FastIron numbers physical ports unit/module/port (e.g. 1/1/8) and derives the
SNMP/sFlow ifIndex as:

    ifIndex = (unit - 1) * 256 + (module - 1) * 64 + port
    LAG n   = 3072 + n

(matches observed switches: 1/1/8 -> 8, LAG 1 -> 3073). Each mapping is
cross-checked against the port speed reported by sFlow counters; a mismatch
marks the mapping as uncertain instead of showing a wrong name.
"""

from __future__ import annotations

import re

_PORT = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{1,3})$")
LAG_BASE = 3072


def port_ifindex(port_id: str) -> int | None:
    m = _PORT.match(port_id.strip())
    if not m:
        return None
    unit, module, port = (int(g) for g in m.groups())
    if unit < 1 or module < 1 or port < 1 or module > 4 or port > 64:
        return None
    return (unit - 1) * 256 + (module - 1) * 64 + port


def lag_ifindex(lag_id: str | int | None) -> int | None:
    try:
        n = int(str(lag_id).strip())
    except (TypeError, ValueError):
        return None
    return LAG_BASE + n if 1 <= n <= 1024 else None


_DEFAULT_IF_NAME = re.compile(r"[\d.]*[A-Za-z]*Ethernet\s*\d{1,2}/\d{1,2}/\d{1,3}", re.IGNORECASE)


def user_port_name(name: str | None) -> str:
    """Drops default interface names ('GigabitEthernet1/1/1', '2.5GigabitEthernet1/1/2',
    '10GigabitEthernet1/3/1'): only names configured by an operator are useful labels."""
    n = (name or "").strip()
    return "" if _DEFAULT_IF_NAME.fullmatch(n) else n


def speed_bps(text: str | None) -> int | None:
    """Parses controller speed strings such as '10 Gb/sec', '1G', '100M', '2.5 Gbps'."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KMGT])", text.upper())
    if not m:
        return None
    mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m.group(2)]
    return int(float(m.group(1)) * mult)
