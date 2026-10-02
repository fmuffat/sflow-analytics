"""MAC address vendor lookup from the IEEE registries (MA-L, MA-M, MA-S).

The database (app/data/oui.tsv.gz) is bundled: no network access at run time.
Refresh it with scripts/update-oui.py.
"""

from __future__ import annotations

import gzip
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "oui.tsv.gz"
_HEX = re.compile(r"[^0-9A-Fa-f]")
_SUFFIXES = re.compile(
    r"[,.]?\s+(incorporated|inc|corporation|corporate|corp|co\.?,?\s*ltd|company|limited|ltd|llc|gmbh|ag|s\.?a\.?|"
    r"b\.?v\.?|s\.?p\.?a\.?|oy|ab|as|srl|sas|pty|plc|kk|technologies|technology|electronics|international)\.?$",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _db() -> dict[int, dict[str, str]]:
    tables: dict[int, dict[str, str]] = {6: {}, 7: {}, 9: {}}
    if not DATA.exists():
        return tables
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        for line in f:
            prefix, _, org = line.rstrip("\n").partition("\t")
            if len(prefix) in tables:
                tables[len(prefix)][prefix] = org
    return tables


def short_name(org: str) -> str:
    """'Synology Incorporated' -> 'Synology', 'Hon Hai Precision Ind. Co.,Ltd.' -> 'Hon Hai Precision Ind.'."""
    name = org.strip()
    for _ in range(3):
        new = _SUFFIXES.sub("", name).strip(" ,")
        if new == name or not new:
            break
        name = new
    return name


def lookup(mac: str | None) -> dict[str, str] | None:
    """{"vendor", "organization"} for a MAC, or a description of special
    addresses (randomized / locally administered, multicast); None if unknown."""
    h = _HEX.sub("", mac or "").upper()
    if len(h) != 12:
        return None
    first = int(h[:2], 16)
    if first & 0x01:
        return {"vendor": "Multicast", "organization": "multicast / broadcast address"}
    if first & 0x02:
        # Phones and laptops use random MACs for privacy: no vendor can be known.
        return {"vendor": "Randomized MAC", "organization": "locally administered address"}
    db = _db()
    for n in (9, 7, 6):
        org = db[n].get(h[:n])
        if org:
            return {"vendor": short_name(org), "organization": org}
    return None


def vendor(mac: str | None) -> str | None:
    r = lookup(mac)
    return r["vendor"] if r else None


def size() -> int:
    return sum(len(t) for t in _db().values())
