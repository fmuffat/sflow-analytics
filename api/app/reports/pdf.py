"""PDF rendering of a report: HTML/CSS template (template.html) + SVG charts, printed by WeasyPrint."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, select_autoescape

from . import charts
from .charts import human_bps, human_bytes

SECTION_TITLES = {
    "summary": "Summary", "ports": "Monitored links and busiest ports", "applications": "Applications and protocols",
    "talkers": "Top talkers and destinations", "conversations": "Top conversations", "trends": "Trends",
    "alerts": "Alerts and incidents",
}
_TEMPLATE = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")


def _change(p: float | None) -> str:
    if p is None:
        return '<span class="muted">–</span>'
    cls = "up" if p > 0 else "down" if p < 0 else "muted"
    arrow = "▲" if p > 0 else "▼" if p < 0 else "="
    return f'<span class="{cls}">{arrow} {"+" if p > 0 else ""}{p:.0f} %</span>' if abs(p) >= 10 else \
        f'<span class="{cls}">{arrow} {"+" if p > 0 else ""}{p:.1f} %</span>'


def _pct(cur: float | None, prev: float | None) -> float | None:
    return None if not prev or cur is None else round((cur - prev) / prev * 100, 1)


def _host(ip: str, name: str | None, vendor: str | None) -> str:
    sub = escape(ip) + (f" · {escape(vendor)}" if vendor else "")
    if name:
        return f'<span class="name">{escape(name)}</span><br><span class="ip">{sub}</span>'
    return f'<span class="ip" style="color:#22262b">{escape(ip)}</span>' + (f'<br><span class="ip">{escape(vendor)}</span>' if vendor else "")


def _host_table(rows: list[dict[str, Any]]) -> str:
    out = ['<table class="t"><tr><th>Host</th><th class="n">Traffic</th><th class="n">Share</th></tr>']
    for r in rows:
        out.append(f'<tr><td>{_host(r["ip"], r.get("name"), r.get("vendor"))}</td>'
                   f'<td class="n">{human_bytes(r["bytes"])}</td><td class="n">{r["percent"]} %</td></tr>')
    out.append("</table>")
    return "".join(out)


def _port_table(rows: list[dict[str, Any]], warn: float, monitored: bool) -> str:
    head = '<tr>' + ('<th>Group</th>' if monitored else '') + \
        '<th>Switch / port</th><th class="n">Speed</th><th class="n">Avg in / out</th><th>95th percentile</th>' \
        '<th class="n">Peak</th><th class="n">vs prev.</th><th class="n">Discards</th></tr>'
    out = ['<table class="t">', head]
    for r in rows:
        if r.get("missing"):
            out.append('<tr>' + (f'<td>{escape(r["group"])}</td>' if monitored else '') +
                       f'<td>{escape(r["label"])}</td><td colspan="6" class="muted">no counters in this period</td></tr>')
            continue
        p95 = r["p95_pct"]
        speed = human_bps(r["speed_bps"]).replace(".0", "") if r.get("speed_bps") else "–"
        delta = "–" if r.get("prev_p95_pct") is None else f'{p95 - r["prev_p95_pct"]:+.1f} pt'
        out.append(
            f'<tr class="{"warn" if r["warn"] else ""}">' + (f'<td>{escape(r.get("group") or "")}</td>' if monitored else '') +
            f'<td><b>{escape(r["exporter_name"])}</b><br>{escape(r["label"])}</td><td class="n">{speed}</td>'
            f'<td class="n">{human_bps(r["in_avg_bps"])}<br>{human_bps(r["out_avg_bps"])}</td>'
            f'<td>{charts.util_bar(p95, warn)} <b>{p95:.1f} %</b></td>'
            f'<td class="n">{r["max_pct"]:.1f} %</td><td class="n">{delta}</td>'
            f'<td class="n">{r["discards"]:,}</td></tr>')
    out.append("</table>")
    return "".join(out)


def _donut(items: list[dict[str, Any]]) -> str:
    key = "service" if items and "service" in items[0] else "protocol"
    top = items[:8]
    rest = sum(i["bytes"] for i in items[8:])
    data = [(str(i.get(key)), i["bytes"]) for i in top] + ([("Other", rest)] if rest else [])
    return charts.donut(data, 140)


def _legend(items: list[dict[str, Any]]) -> str:
    key = "service" if items and "service" in items[0] else "protocol"
    out = ['<table class="legend">']
    for n, i in enumerate(items[:8]):
        out.append(f'<tr><td><span class="sw" style="background:{charts.PALETTE[n % len(charts.PALETTE)]}"></span></td>'
                   f'<td>{escape(str(i.get(key)))}</td><td style="text-align:right">{i["percent"]} %</td></tr>')
    out.append("</table>")
    return "".join(out)


def _duration(s: int) -> str:
    if s < 3600:
        return f"{max(1, s // 60)} min"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d}"
    return f"{s // 86400} d {s % 86400 // 3600} h"


def render_html(r: dict[str, Any]) -> str:
    tz = ZoneInfo(r["tz"])

    def localtime(v: Any) -> str:
        if not v:
            return "–"
        d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(tz).strftime("%a %d %b %H:%M")

    env = Environment(autoescape=select_autoescape(default_for_string=True))
    env.globals.update(
        r=r, SECTION_TITLES=SECTION_TITLES, bytes=human_bytes, bps=human_bps, change=_change, pct=_pct,
        donut=_donut, legend=_legend, host=_host, host_table=_host_table, port_table=_port_table,
        bars=charts.bars_compare, localtime=localtime, duration=_duration,
        num=lambda section: r["sections"].index(section) + 1)
    return env.from_string(_TEMPLATE).render()


def render_pdf(r: dict[str, Any]) -> bytes:
    from weasyprint import HTML  # heavy import, only when a PDF is produced

    return HTML(string=render_html(r)).write_pdf()
