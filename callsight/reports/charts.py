"""Simple SVG bar charts for the PDF (no JS, no browser). Labels are Latin digits only; Arabic stays in HTML."""
from __future__ import annotations

from datetime import date
from html import escape

OK, BAD, GRID, INK = "#1e7a46", "#b3261e", "#e3e6ec", "#687083"


def _nice_max(v: int) -> int:
    """Axis top divisible by 4, so the 4 grid steps are whole numbers."""
    for top in (4, 8, 12, 20, 40, 60, 100, 200, 400, 600, 1000, 2000, 4000):
        if v <= top:
            return top
    return -(-v // 4) * 4


def day_bars(days: list[dict], width: int = 680, height: int = 190) -> str:
    """Stacked bars per day (answered + unanswered), first day on the RIGHT (RTL reading order)."""
    n = max(len(days), 1)
    top, bottom, side = 12, 24, 28
    plot_h, plot_w = height - top - bottom, width - 2 * side
    peak = _nice_max(max((d["answered"] + d["unanswered"] for d in days), default=0) or 1)   # bars stack both
    slot = plot_w / n
    bar = max(2.0, slot * 0.62)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" font-family="IBM Plex Sans Arabic" font-size="10">']
    for i in range(5):   # grid lines + values on the right edge
        y = top + plot_h * i / 4
        parts.append(f'<line x1="{side}" y1="{y:.1f}" x2="{width - side}" y2="{y:.1f}" stroke="{GRID}"/>')
        parts.append(f'<text x="{width - side + 4}" y="{y + 3:.1f}" fill="{INK}">{peak * (4 - i) // 4}</text>')
    label_every = 1 if n <= 10 else (2 if n <= 20 else 3)
    for i, d in enumerate(days):
        x = width - side - slot * (i + 1) + (slot - bar) / 2
        ans_h = plot_h * d["answered"] / peak
        un_h = plot_h * d["unanswered"] / peak
        base = top + plot_h
        if ans_h:
            parts.append(f'<rect x="{x:.1f}" y="{base - ans_h:.1f}" width="{bar:.1f}" height="{ans_h:.1f}" fill="{OK}"/>')
        if un_h:
            parts.append(f'<rect x="{x:.1f}" y="{base - ans_h - un_h:.1f}" width="{bar:.1f}" height="{un_h:.1f}" '
                         f'fill="{BAD}"/>')
        if i % label_every == 0:
            day: date = d["day"]
            parts.append(f'<text x="{x + bar / 2:.1f}" y="{height - 8}" text-anchor="middle" fill="{INK}">'
                         f'{day.day}/{day.month}</text>')
    parts.append("</svg>")
    return "".join(parts)


def hbar(value: int, total: int, color: str = OK, width: int = 160, height: int = 12) -> str:
    """One horizontal bar (share of total), anchored on the right for RTL tables."""
    w = width * value / total if total else 0
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="{GRID}"/>'
            f'<rect x="{width - w:.1f}" y="0" width="{w:.1f}" height="{height}" fill="{escape(color)}"/></svg>')
