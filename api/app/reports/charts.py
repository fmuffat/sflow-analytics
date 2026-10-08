"""Small SVG charts for the PDF report (no plotting library: crisp vector output in WeasyPrint)."""

from __future__ import annotations

import math
from html import escape

BRAND = "#f58220"
PALETTE = ["#f58220", "#2f6db5", "#3a9d4a", "#8e44ad", "#e2a21b", "#17a2a2", "#d64545", "#6c7a89", "#b5651d", "#2c3e50"]
GREY = "#b9bfc6"
TEXT = "#22262b"
MUTED = "#6b7480"


def human_bytes(v: float | None) -> str:
    if v is None:
        return "–"
    for unit in ("B", "kB", "MB", "GB", "TB", "PB"):
        if abs(v) < 1000 or unit == "PB":
            return f"{v:.0f} {unit}" if unit == "B" or abs(v) >= 100 else f"{v:.1f} {unit}"
        v /= 1000
    return ""


def human_bps(v: float | None) -> str:
    if v is None:
        return "–"
    for unit in ("b/s", "kb/s", "Mb/s", "Gb/s", "Tb/s"):
        if abs(v) < 1000 or unit == "Tb/s":
            return f"{v:.0f} {unit}" if unit == "b/s" or abs(v) >= 100 else f"{v:.1f} {unit}"
        v /= 1000
    return ""


def donut(items: list[tuple[str, float]], size: int = 150) -> str:
    """Donut chart; items are (label, value)."""
    total = sum(v for _, v in items) or 1
    r, cx, cy, w = size / 2 - 8, size / 2, size / 2, 26
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">']
    a0 = -math.pi / 2
    for i, (_, v) in enumerate(items):
        frac = v / total
        if frac <= 0:
            continue
        if frac >= 0.9999:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{r - w / 2}" fill="none" stroke="{PALETTE[i % len(PALETTE)]}" stroke-width="{w}"/>')
            break
        a1 = a0 + frac * 2 * math.pi
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        ri = r - w
        xi1, yi1 = cx + ri * math.cos(a1), cy + ri * math.sin(a1)
        xi0, yi0 = cx + ri * math.cos(a0), cy + ri * math.sin(a0)
        large = 1 if a1 - a0 > math.pi else 0
        out.append(f'<path d="M{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f} L{xi1:.2f},{yi1:.2f} '
                   f'A{ri},{ri} 0 {large} 0 {xi0:.2f},{yi0:.2f} Z" fill="{PALETTE[i % len(PALETTE)]}"/>')
        a0 = a1
    out.append("</svg>")
    return "".join(out)


def bars_compare(points: list[dict], width: int = 680, height: int = 190) -> str:
    """Grouped bars per bucket: current period (brand) and previous period (grey)."""
    if not points:
        return ""
    vmax = max([p["bytes"] or 0 for p in points] + [p["prev_bytes"] or 0 for p in points]) or 1
    left, bottom, top = 58, 22, 8
    ph = height - bottom - top
    n = len(points)
    slot = (width - left - 6) / n
    bw = max(2.0, min(22.0, slot * 0.36))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="DejaVu Sans" font-size="9">']
    for k in range(5):
        y = top + ph - ph * k / 4
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - 4}" y2="{y:.1f}" stroke="#e3e6ea" stroke-width="0.6"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" fill="{MUTED}">{escape(human_bytes(vmax * k / 4))}</text>')
    label_every = max(1, math.ceil(n / 16))
    for i, p in enumerate(points):
        x = left + i * slot + slot / 2
        for j, (v, color) in enumerate(((p["prev_bytes"], GREY), (p["bytes"], BRAND))):
            if v:
                h = ph * v / vmax
                out.append(f'<rect x="{x - bw + j * bw:.1f}" y="{top + ph - h:.1f}" width="{bw - 1:.1f}" height="{h:.1f}" fill="{color}"/>')
        if i % label_every == 0:
            out.append(f'<text x="{x:.1f}" y="{height - 6}" text-anchor="middle" fill="{MUTED}">{escape(p["label"])}</text>')
    out.append("</svg>")
    return "".join(out)


def util_bar(pct: float | None, warn: float, width: int = 110) -> str:
    """Horizontal utilization bar (0-100 %), orange from the warning threshold, red from 90 %."""
    pct = max(0.0, min(100.0, pct or 0.0))
    color = "#d64545" if pct >= 90 else BRAND if pct >= warn else "#3a9d4a"
    w = max(1.0, width * pct / 100) if pct > 0 else 0
    marker = width * warn / 100
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="9" viewBox="0 0 {width} 9">'
            f'<rect x="0" y="1" width="{width}" height="7" rx="2" fill="#eef0f2"/>'
            f'<rect x="0" y="1" width="{w:.1f}" height="7" rx="2" fill="{color}"/>'
            f'<line x1="{marker:.1f}" y1="0" x2="{marker:.1f}" y2="9" stroke="{MUTED}" stroke-width="0.7"/></svg>')
