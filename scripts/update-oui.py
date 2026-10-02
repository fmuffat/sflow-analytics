#!/usr/bin/env python3
"""Builds api/app/data/oui.tsv.gz (MAC prefix -> vendor) from the IEEE registries.

    python3 scripts/update-oui.py [dir-with-csv-files]

Without an argument the three public registries are downloaded from
https://standards-oui.ieee.org (MA-L oui.csv, MA-M mam.csv, MA-S oui36.csv).
Output: one "PREFIX<TAB>Organization" line per assignment, prefix as upper-case
hex digits (6 for MA-L, 7 for MA-M, 9 for MA-S), sorted, gzip-compressed.
"""

import csv
import gzip
import io
import pathlib
import sys
import urllib.request

SOURCES = {"oui.csv": "oui/oui.csv", "mam.csv": "oui28/mam.csv", "oui36.csv": "oui36/oui36.csv"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "api/app/data/oui.tsv.gz"


def read(name: str, src_dir: pathlib.Path | None) -> str:
    if src_dir:
        return (src_dir / name).read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request("https://standards-oui.ieee.org/" + SOURCES[name],
                                 headers={"User-Agent": "sflow-analytics OUI update"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", errors="replace")


def main() -> int:
    src_dir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    entries: dict[str, str] = {}
    for name in SOURCES:
        for row in csv.DictReader(io.StringIO(read(name, src_dir))):
            prefix = (row.get("Assignment") or "").strip().upper()
            org = " ".join((row.get("Organization Name") or "").split())
            if prefix and org and all(c in "0123456789ABCDEF" for c in prefix):
                entries[prefix] = org
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(f"{p}\t{o}\n" for p, o in sorted(entries.items())).encode()
    with gzip.GzipFile(OUT, "wb", mtime=0) as f:  # mtime=0: reproducible file
        f.write(data)
    print(f"{len(entries)} prefixes -> {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
