# -*- coding: utf-8 -*-
"""
reports/charts.py
~~~~~~~~~~~~~~~~~
SVG chart generators for the Jira status pie and bar charts.
Produces self-contained, dark-themed SVG embedded as base64 data URIs.
"""

import base64
import html
import math

from core.config import JIRA_STATUS_ORDER


# ---------------------------------------------------------------------------
# Colour palette & helpers
# ---------------------------------------------------------------------------

_PALETTE = [
    "#2ec4b6", "#3a86ff", "#ff9f1c", "#ff6b6b",
    "#8e7dff", "#52d273", "#f15bb5", "#00bbf9",
    "#f94144", "#90be6d",
]


def _chart_palette(index: int) -> str:
    return _PALETTE[index % len(_PALETTE)]


def _compact_chart_items(items: list, max_items: int = 6) -> list:
    ordered = [(label, int(value)) for label, value in items if int(value) > 0]
    ordered.sort(key=lambda item: (-item[1], item[0].lower()))
    if len(ordered) <= max_items:
        return ordered
    head = ordered[: max_items - 1]
    other_total = sum(v for _, v in ordered[max_items - 1 :])
    head.append(("Other", other_total))
    return head


def _svg_data_uri(svg_markup: str) -> str:
    encoded = base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _build_pie_chart_svg(title: str, items: list) -> str:
    items = _compact_chart_items(items, max_items=6)
    total = sum(v for _, v in items)
    width, height = 720, 380
    cx, cy, outer_r, inner_r = 170, 190, 120, 72

    if total <= 0:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            f'<rect width="100%" height="100%" rx="20" fill="#0f1720"/>'
            f'<text x="32" y="46" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="24" font-weight="700">{html.escape(title)}</text>'
            f'<text x="32" y="92" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="16">No chart data available</text></svg>'
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="20" fill="#0f1720"/>',
        f'<text x="32" y="46" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="24" font-weight="700">{html.escape(title)}</text>',
        f'<text x="32" y="82" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="14">Total items: {total}</text>',
    ]

    current_angle = -math.pi / 2
    for idx, (label, value) in enumerate(items):
        angle = (value / total) * math.tau
        end_angle = current_angle + angle
        large_arc = 1 if angle > math.pi else 0
        x1 = cx + outer_r * math.cos(current_angle)
        y1 = cy + outer_r * math.sin(current_angle)
        x2 = cx + outer_r * math.cos(end_angle)
        y2 = cy + outer_r * math.sin(end_angle)
        x3 = cx + inner_r * math.cos(end_angle)
        y3 = cy + inner_r * math.sin(end_angle)
        x4 = cx + inner_r * math.cos(current_angle)
        y4 = cy + inner_r * math.sin(current_angle)
        path = (
            f"M {x1:.2f} {y1:.2f} "
            f"A {outer_r} {outer_r} 0 {large_arc} 1 {x2:.2f} {y2:.2f} "
            f"L {x3:.2f} {y3:.2f} "
            f"A {inner_r} {inner_r} 0 {large_arc} 0 {x4:.2f} {y4:.2f} Z"
        )
        parts.append(f'<path d="{path}" fill="{_chart_palette(idx)}" stroke="#0f1720" stroke-width="2"/>')
        current_angle = end_angle

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner_r - 8}" fill="#0f1720"/>')
    parts.append(f'<text x="{cx}" y="{cy - 6}" text-anchor="middle" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="30" font-weight="700">{total}</text>')
    parts.append(f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="14">work items</text>')

    legend_x, legend_y = 380, 110
    for idx, (label, value) in enumerate(items):
        y = legend_y + idx * 42
        parts.append(f'<rect x="{legend_x}" y="{y - 12}" width="14" height="14" rx="3" fill="{_chart_palette(idx)}"/>')
        parts.append(f'<text x="{legend_x + 22}" y="{y}" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="16">{html.escape(str(label))}</text>')
        parts.append(f'<text x="{width - 32}" y="{y}" text-anchor="end" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="16">{value}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _build_bar_chart_svg(title: str, items: list) -> str:
    items = _compact_chart_items(items, max_items=6)
    width, height = 720, 380
    left, top, chart_width, bar_height, gap = 200, 78, 440, 26, 18

    if not items:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
            f'<rect width="100%" height="100%" rx="20" fill="#0f1720"/>'
            f'<text x="32" y="46" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="24" font-weight="700">{html.escape(title)}</text>'
            f'<text x="32" y="92" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" '
            f'font-size="16">No chart data available</text></svg>'
        )

    max_value = max(v for _, v in items)
    rows_height = len(items) * (bar_height + gap)
    total_height = top + rows_height + 34
    if total_height > height:
        height = total_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="20" fill="#0f1720"/>',
        f'<text x="32" y="46" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="24" font-weight="700">{html.escape(title)}</text>',
    ]

    for idx, (label, value) in enumerate(items):
        y = top + idx * (bar_height + gap)
        bar_len = 0 if max_value <= 0 else max(6, (value / max_value) * chart_width)
        parts.append(f'<text x="32" y="{y + 19}" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="16">{html.escape(str(label))}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{chart_width}" height="{bar_height}" rx="13" fill="#20313c"/>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_len:.2f}" height="{bar_height}" rx="13" fill="{_chart_palette(idx)}"/>')
        parts.append(f'<text x="{left + chart_width + 16}" y="{y + 19}" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="16">{value}</text>')

    parts.append("</svg>")
    return "".join(parts)


def build_report_charts(jira_summary: dict) -> dict:
    """Build pie and bar chart data URIs from jira_summary status counts."""
    status_counts = jira_summary.get("canonical_status_counts", {}) or {}
    chart_items = [(label, int(status_counts.get(label, 0))) for label in JIRA_STATUS_ORDER]
    chart_items = [(label, count) for label, count in chart_items if count > 0]

    if not chart_items:
        chart_items = [
            ("Completed", jira_summary.get("completed", 0)),
            ("Blocked", jira_summary.get("blocked", 0)),
            ("Open", max(0, jira_summary.get("total_tasks", 0) - jira_summary.get("completed", 0) - jira_summary.get("blocked", 0))),
        ]

    pie_svg = _build_pie_chart_svg("Jira Status Overview", chart_items)
    bar_svg = _build_bar_chart_svg("Status Count by Workflow State", chart_items)

    return {
        "pie": {
            "title": "Jira Status Overview",
            "data_uri": _svg_data_uri(pie_svg),
            "labels": [label for label, _ in _compact_chart_items(chart_items, max_items=6)],
        },
        "bar": {
            "title": "Status Count by Workflow State",
            "data_uri": _svg_data_uri(bar_svg),
            "labels": [label for label, _ in _compact_chart_items(chart_items, max_items=6)],
        },
    }
