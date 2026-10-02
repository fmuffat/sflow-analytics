"""CSV export of analytics results (same data as the JSON `items`)."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi.responses import Response


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return " ".join(str(x.get("name", x.get("id")) if isinstance(x, dict) else x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, default=str)
    return v


def to_csv(items: list[dict[str, Any]], filename: str) -> Response:
    buf = io.StringIO()
    if items:
        cols = list(items[0].keys())
        w = csv.writer(buf)
        w.writerow(cols)
        for it in items:
            w.writerow([_cell(it.get(c)) for c in cols])
    return Response(
        buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


def respond(data: dict[str, Any], fmt: str, name: str):
    """JSON envelope as is, or its items as CSV."""
    return to_csv(data["items"], name) if fmt == "csv" else data
