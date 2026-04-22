# -*- coding: utf-8 -*-
import base64
import html
import math
import os
import re
import sys
import json
import platform
from datetime import datetime, timezone
from datetime import timedelta
from collections import Counter
from itertools import islice
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from dotenv import load_dotenv
from jira import JIRA
from github import Auth, Github
from github.GithubException import BadCredentialsException
from agentic_engine import (
    run_agentic_planner, _risk_facts, _load_memory_state, _memory_feedback,
    execute_actions, _evaluate_action_outcomes, _derive_escalation_actions,
    _sort_actions_by_priority, _select_previous_executed_run, _select_trend_baseline_run,
    _trend_analysis, _memory_max_runs, _save_memory_state, _memory_file_path
)
from observability import METRICS, log_event

# ---- SAFE UNICODE PRINT (handles Windows cp1252) ----
def safe_print(message):
    """Print with fallback for encoding errors (Windows cp1252 compatibility)"""
    try:
        print(message)
    except UnicodeEncodeError:
        # Replace emojis with text alternatives for Windows console
        fallback = message
        fallback = fallback.replace("🛰️", "[SAT]").replace("🔍", "[SEARCH]").replace("✅", "[OK]")
        fallback = fallback.replace("🧬", "[DNA]").replace("⚠️", "[WARN]").replace("❌", "[ERR]")
        fallback = fallback.replace("🔌", "[PLUG]").replace("📦", "[BOX]").replace("📊", "[CHART]")
        fallback = fallback.replace("🛑", "[STOP]").replace("-", "-")
        print(fallback, file=sys.stderr)

# ---- CONFIG ----
load_dotenv()

JIRA_SERVER = os.getenv("JIRA_SERVER")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")

GH_TOKEN = os.getenv("GH_TOKEN")
GH_REPO = os.getenv("GH_REPO")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_IDS = os.getenv("SLACK_CHANNEL_IDS", "")
try:
    SLACK_LOOKBACK_DAYS = int(os.getenv("SLACK_LOOKBACK_DAYS", "7"))
except ValueError:
    SLACK_LOOKBACK_DAYS = 7

try:
    SLACK_MESSAGE_LIMIT = int(os.getenv("SLACK_MESSAGE_LIMIT", "250"))
except ValueError:
    SLACK_MESSAGE_LIMIT = 250

# ⚠️ Update if needed after debug
STORY_POINTS_FIELD = "customfield_10018"
SUPPORTED_PYTHON_MIN = (3, 10)
SUPPORTED_PYTHON_MAX = (3, 14)

SLACK_KEYWORD_GROUPS = {
    "issues": [
        r"deployment failed",
        r"build failed",
        r"pr blocked",
        r"blocked",
        r"bug found",
        r"regression",
        r"test fail",
        r"qa fail",
        r"rollback",
        r"incident",
        r"outage",
        r"hotfix",
    ],
    "successes": [
        r"deployed successfully",
        r"fixed",
        r"resolved",
        r"merged",
        r"shipped",
        r"released",
        r"passed",
        r"completed",
        r"done",
        r"approved",
    ],
    "delivery_risks": [
        r"review pending",
        r"waiting for review",
        r"stuck",
        r"blocked",
        r"missing",
        r"dependency",
        r"conflict",
        r"permission",
    ],
}

JIRA_STATUS_ORDER = [
    "To Do",
    "Open",
    "Dev In Progress",
    "QA In Progress",
    "Peer Review",
    "Peer Accepted",
    "Completed",
    "Blocked",
]

JIRA_STATUS_ALIASES = {
    "to do": "To Do",
    "todo": "To Do",
    "open": "Open",
    "dev in progress": "Dev In Progress",
    "development in progress": "Dev In Progress",
    "in progress": "Dev In Progress",
    "qa in progress": "QA In Progress",
    "qa": "QA In Progress",
    "testing": "QA In Progress",
    "peer review": "Peer Review",
    "code review": "Peer Review",
    "review": "Peer Review",
    "peer accepted": "Peer Accepted",
    "done": "Completed",
    "closed": "Completed",
    "resolved": "Completed",
    "completed": "Completed",
    "blocked": "Blocked",
}

# ---------------- VALIDATION ----------------
def validate_config():
    if not all([JIRA_SERVER, JIRA_EMAIL, JIRA_TOKEN, GH_TOKEN, GH_REPO, GROQ_API_KEY]):
        raise RuntimeError("❌ Missing environment variables. Check your .env file.")


def runtime_support_status():
    current = (sys.version_info.major, sys.version_info.minor)
    supported = SUPPORTED_PYTHON_MIN <= current <= SUPPORTED_PYTHON_MAX
    return {
        "supported": supported,
        "current": f"{current[0]}.{current[1]}",
        "recommended": "3.12",
        "supported_range": f">={SUPPORTED_PYTHON_MIN[0]}.{SUPPORTED_PYTHON_MIN[1]} and <={SUPPORTED_PYTHON_MAX[0]}.{SUPPORTED_PYTHON_MAX[1]}",
        "platform": platform.platform(),
    }


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _normalize_jira_status(status_name):
    normalized = str(status_name or "").strip().lower()
    if not normalized:
        return "Open"

    if normalized in JIRA_STATUS_ALIASES:
        return JIRA_STATUS_ALIASES[normalized]

    for alias, canonical in JIRA_STATUS_ALIASES.items():
        if alias in normalized:
            return canonical

    return str(status_name).strip() or "Open"


def _slack_config_mode():
    if SLACK_BOT_TOKEN and SLACK_CHANNEL_IDS.strip():
        return "slack_api"
    return "not_configured"


def _slack_channel_name(channel_id):
    if not channel_id:
        return "unknown"
    return str(channel_id).strip() or "unknown"


def _slack_date_bucket(message):
    ts = message.get("ts")
    parsed = None
    if ts is not None:
        try:
            parsed = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            parsed = None

    if parsed is None:
        return "unknown-date"

    return parsed.strftime("%Y-%m-%d")


def _fetch_slack_channel_history(token, channel_id, oldest_ts, limit):
    params = {
        "channel": channel_id,
        "limit": str(limit),
    }
    if oldest_ts:
        params["oldest"] = str(oldest_ts)

    url = "https://slack.com/api/conversations.history?" + urllib_parse.urlencode(params)
    request = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    with urllib_request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "slack_api_error"))

    messages = []
    for item in payload.get("messages", []):
        text = str(item.get("text", "")).strip()
        if not text or item.get("subtype") == "channel_join":
            continue
        messages.append({
            "text": text,
            "ts": item.get("ts"),
            "user": item.get("user") or item.get("bot_id") or "unknown",
            "channel": channel_id,
            "source": "slack_api",
        })

    return messages


def fetch_slack_messages():
    if not (SLACK_BOT_TOKEN and SLACK_CHANNEL_IDS.strip()):
        return [], []

    oldest_ts = int((datetime.now(timezone.utc) - timedelta(days=SLACK_LOOKBACK_DAYS)).timestamp())
    messages = []
    fetch_diagnostics = []

    for channel_id in [channel.strip() for channel in SLACK_CHANNEL_IDS.split(",") if channel.strip()]:
        try:
            channel_messages = _fetch_slack_channel_history(SLACK_BOT_TOKEN, channel_id, oldest_ts, SLACK_MESSAGE_LIMIT)
            messages.extend(channel_messages)
            fetch_diagnostics.append({
                "channel": channel_id,
                "status": "ok",
                "fetched_messages": len(channel_messages),
                "error": None,
            })
        except RuntimeError as exc:
            METRICS.record_external_api_failure("slack")
            log_event("warning", "slack_fetch_failed", channel=channel_id, error_message=str(exc))
            fetch_diagnostics.append({
                "channel": channel_id,
                "status": "error",
                "fetched_messages": 0,
                "error": str(exc),
            })
        except (urllib_error.URLError, urllib_error.HTTPError) as exc:
            METRICS.record_external_api_failure("slack")
            log_event("warning", "slack_fetch_http_failed", channel=channel_id, error_type=type(exc).__name__, error_message=str(exc))
            fetch_diagnostics.append({
                "channel": channel_id,
                "status": "error",
                "fetched_messages": 0,
                "error": str(exc),
            })

    return messages, fetch_diagnostics


def build_slack_week_summary(analyzed_messages, total_messages, category_counter):
    if not analyzed_messages:
        if total_messages == 0:
            if _slack_config_mode() == "slack_api":
                return "Slack API is configured, but no messages were retrieved in the selected lookback window."
            return "No Slack conversation data was provided for this report cycle."
        return "Slack data was received, but no delivery-related keywords were matched in the selected conversation window."

    issue_count = category_counter.get("issues", 0)
    success_count = category_counter.get("successes", 0)
    risk_count = category_counter.get("delivery_risks", 0)

    top_issue_texts = [item["text"] for item in analyzed_messages if "issues" in item["matched_categories"]][:3]
    top_success_texts = [item["text"] for item in analyzed_messages if "successes" in item["matched_categories"]][:3]
    top_risk_texts = [item["text"] for item in analyzed_messages if "delivery_risks" in item["matched_categories"]][:3]

    summary_parts = [
        f"Reviewed {total_messages} Slack messages for the week.",
        f"Detected {issue_count} issue-related mentions, {success_count} success mentions, and {risk_count} delivery-risk mentions.",
    ]

    if top_issue_texts:
        summary_parts.append("Main issue signals: " + " | ".join(top_issue_texts))
    if top_success_texts:
        summary_parts.append("Notable wins: " + " | ".join(top_success_texts))
    if top_risk_texts:
        summary_parts.append("Delivery risks: " + " | ".join(top_risk_texts))

    return " ".join(summary_parts)


def build_slack_grouped_summary(analyzed_messages):
    grouped_by_channel = {}
    grouped_by_date = {}

    for item in analyzed_messages:
        channel_key = _slack_channel_name(item.get("channel", "unknown"))
        date_key = _slack_date_bucket(item)

        grouped_by_channel.setdefault(channel_key, [])
        grouped_by_channel[channel_key].append(item)

        grouped_by_date.setdefault(date_key, [])
        grouped_by_date[date_key].append(item)

    channel_summary = []
    for channel_name, items in sorted(grouped_by_channel.items(), key=lambda entry: (-len(entry[1]), entry[0])):
        category_counter = Counter()
        for item in items:
            category_counter.update(item.get("matched_categories", []))

        channel_summary.append({
            "channel": channel_name,
            "matched_messages": len(items),
            "category_counts": dict(category_counter),
            "highlights": [item["text"] for item in items[:3]],
        })

    date_summary = []
    for date_name, items in sorted(grouped_by_date.items(), key=lambda entry: entry[0], reverse=True):
        category_counter = Counter()
        for item in items:
            category_counter.update(item.get("matched_categories", []))

        date_summary.append({
            "date": date_name,
            "matched_messages": len(items),
            "category_counts": dict(category_counter),
            "highlights": [item["text"] for item in items[:3]],
        })

    return {
        "by_channel": channel_summary,
        "by_date": date_summary,
    }


def analyze_slack_messages(messages, fetch_diagnostics=None):
    analyzed_messages = []
    category_counter = Counter()
    keyword_counter = Counter()
    fetch_diagnostics = fetch_diagnostics or []
    configured = _slack_config_mode() == "slack_api"

    patterns = {
        category: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for category, patterns in SLACK_KEYWORD_GROUPS.items()
    }

    for message in messages:
        text = str(message.get("text", "")).strip()
        if not text:
            continue

        matched_categories = []
        matched_keywords = []
        for category, compiled_patterns in patterns.items():
            for compiled_pattern in compiled_patterns:
                if compiled_pattern.search(text):
                    matched_categories.append(category)
                    matched_keywords.append(compiled_pattern.pattern)
                    category_counter[category] += 1
                    keyword_counter[compiled_pattern.pattern] += 1

        if matched_categories:
            analyzed_messages.append({
                "text": text,
                "user": message.get("user", "unknown"),
                "channel": message.get("channel", "unknown"),
                "ts": message.get("ts"),
                "matched_categories": sorted(set(matched_categories)),
                "matched_keywords": matched_keywords,
            })

    issue_messages = [item for item in analyzed_messages if "issues" in item["matched_categories"]]
    success_messages = [item for item in analyzed_messages if "successes" in item["matched_categories"]]
    risk_messages = [item for item in analyzed_messages if "delivery_risks" in item["matched_categories"]]
    failed_channels = [item for item in fetch_diagnostics if item.get("status") != "ok"]

    return {
        "enabled": configured,
        "source": _slack_config_mode(),
        "message_count": len(messages),
        "matched_message_count": len(analyzed_messages),
        "channels_requested": [item.get("channel") for item in fetch_diagnostics],
        "channels_failed": len(failed_channels),
        "fetch_diagnostics": fetch_diagnostics,
        "category_counts": dict(category_counter),
        "top_keywords": keyword_counter.most_common(8),
        "issue_messages": issue_messages[:8],
        "success_messages": success_messages[:8],
        "risk_messages": risk_messages[:8],
        "week_summary": build_slack_week_summary(analyzed_messages, len(messages), category_counter),
        "grouped_summary": build_slack_grouped_summary(analyzed_messages),
    }


def build_compact_slack_summary(slack_summary):
    grouped_summary = slack_summary.get("grouped_summary", {})
    by_channel = grouped_summary.get("by_channel", [])
    by_date = grouped_summary.get("by_date", [])

    compact_by_channel = [
        {
            "channel": item.get("channel"),
            "matched_messages": item.get("matched_messages", 0),
            "category_counts": item.get("category_counts", {}),
        }
        for item in by_channel[:3]
    ]

    compact_by_date = [
        {
            "date": item.get("date"),
            "matched_messages": item.get("matched_messages", 0),
            "category_counts": item.get("category_counts", {}),
        }
        for item in by_date[:5]
    ]

    return {
        "enabled": slack_summary.get("enabled", False),
        "source": slack_summary.get("source", "not_configured"),
        "message_count": slack_summary.get("message_count", 0),
        "matched_message_count": slack_summary.get("matched_message_count", 0),
        "channels_requested": slack_summary.get("channels_requested", []),
        "channels_failed": slack_summary.get("channels_failed", 0),
        "category_counts": slack_summary.get("category_counts", {}),
        "top_keywords": slack_summary.get("top_keywords", [])[:5],
        "issue_highlights": [item.get("text") for item in slack_summary.get("issue_messages", [])[:2]],
        "success_highlights": [item.get("text") for item in slack_summary.get("success_messages", [])[:2]],
        "risk_highlights": [item.get("text") for item in slack_summary.get("risk_messages", [])[:2]],
        "week_summary": slack_summary.get("week_summary", ""),
        "grouped_summary": {
            "by_channel": compact_by_channel,
            "by_date": compact_by_date,
        },
    }


def print_slack_config_warning():
    if _slack_config_mode() == "not_configured":
        safe_print("⚠️ Slack is not configured. Add SLACK_BOT_TOKEN and SLACK_CHANNEL_IDS to include Slack conversation summaries.")


def _svg_data_uri(svg_markup):
    encoded = base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _chart_palette(index):
    palette = [
        "#2ec4b6",
        "#3a86ff",
        "#ff9f1c",
        "#ff6b6b",
        "#8e7dff",
        "#52d273",
        "#f15bb5",
        "#00bbf9",
        "#f94144",
        "#90be6d",
    ]
    return palette[index % len(palette)]


def _compact_chart_items(items, max_items=6):
    ordered = [(label, int(value)) for label, value in items if int(value) > 0]
    ordered.sort(key=lambda item: (-item[1], item[0].lower()))
    if len(ordered) <= max_items:
        return ordered

    head = ordered[: max_items - 1]
    other_total = sum(value for _, value in ordered[max_items - 1 :])
    head.append(("Other", other_total))
    return head


def _build_pie_chart_svg(title, items):
    items = _compact_chart_items(items, max_items=6)
    total = sum(value for _, value in items)
    width = 720
    height = 380
    cx = 170
    cy = 190
    outer_r = 120
    inner_r = 72

    if total <= 0:
        return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">
<rect width=\"100%\" height=\"100%\" rx=\"20\" fill=\"#0f1720\"/>
<text x=\"32\" y=\"46\" fill=\"#e7f2f7\" font-family=\"Segoe UI, Arial, sans-serif\" font-size=\"24\" font-weight=\"700\">{html.escape(title)}</text>
<text x=\"32\" y=\"92\" fill=\"#92a7b2\" font-family=\"Segoe UI, Arial, sans-serif\" font-size=\"16\">No chart data available</text>
</svg>"""

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="20" fill="#0f1720"/>',
        f'<text x="32" y="46" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="24" font-weight="700">{html.escape(title)}</text>',
        f'<text x="32" y="82" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="14">Total items: {total}</text>',
    ]

    current_angle = -math.pi / 2
    for index, (label, value) in enumerate(items):
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
        parts.append(f'<path d="{path}" fill="{_chart_palette(index)}" stroke="#0f1720" stroke-width="2"/>')
        current_angle = end_angle

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner_r - 8}" fill="#0f1720"/>')
    parts.append(f'<text x="{cx}" y="{cy - 6}" text-anchor="middle" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="30" font-weight="700">{total}</text>')
    parts.append(f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="14">work items</text>')

    legend_x = 380
    legend_y = 110
    for index, (label, value) in enumerate(items):
        y = legend_y + index * 42
        parts.append(f'<rect x="{legend_x}" y="{y - 12}" width="14" height="14" rx="3" fill="{_chart_palette(index)}"/>')
        parts.append(f'<text x="{legend_x + 22}" y="{y}" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="16">{html.escape(str(label))}</text>')
        parts.append(f'<text x="{width - 32}" y="{y}" text-anchor="end" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="16">{value}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _build_bar_chart_svg(title, items):
    items = _compact_chart_items(items, max_items=6)
    width = 720
    height = 380
    left = 200
    top = 78
    chart_width = 440
    bar_height = 26
    gap = 18

    if not items:
        return f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">
<rect width=\"100%\" height=\"100%\" rx=\"20\" fill=\"#0f1720\"/>
<text x=\"32\" y=\"46\" fill=\"#e7f2f7\" font-family=\"Segoe UI, Arial, sans-serif\" font-size=\"24\" font-weight=\"700\">{html.escape(title)}</text>
<text x=\"32\" y=\"92\" fill=\"#92a7b2\" font-family=\"Segoe UI, Arial, sans-serif\" font-size=\"16\">No chart data available</text>
</svg>"""

    max_value = max(value for _, value in items)
    rows_height = len(items) * (bar_height + gap)
    total_height = top + rows_height + 34
    if total_height > height:
        height = total_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="20" fill="#0f1720"/>',
        f'<text x="32" y="46" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="24" font-weight="700">{html.escape(title)}</text>',
    ]

    for index, (label, value) in enumerate(items):
        y = top + index * (bar_height + gap)
        bar_len = 0 if max_value <= 0 else max(6, (value / max_value) * chart_width)
        parts.append(f'<text x="32" y="{y + 19}" fill="#e7f2f7" font-family="Segoe UI, Arial, sans-serif" font-size="16">{html.escape(str(label))}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{chart_width}" height="{bar_height}" rx="13" fill="#20313c"/>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_len:.2f}" height="{bar_height}" rx="13" fill="{_chart_palette(index)}"/>')
        parts.append(f'<text x="{left + chart_width + 16}" y="{y + 19}" fill="#92a7b2" font-family="Segoe UI, Arial, sans-serif" font-size="16">{value}</text>')

    parts.append("</svg>")
    return "".join(parts)


def build_report_charts(jira_summary):
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


def print_slack_fetch_diagnostics(slack_summary):
    if _slack_config_mode() != "slack_api":
        return

    channels = slack_summary.get("channels_requested", [])
    if channels:
        safe_print(f"🔌 Slack API channels configured: {', '.join(channels)}")

    failed_channels = [item for item in slack_summary.get("fetch_diagnostics", []) if item.get("status") != "ok"]
    if failed_channels:
        for item in failed_channels:
            safe_print(f"⚠️ Slack fetch failed for channel {item.get('channel')}: {item.get('error')}")
        return

    if slack_summary.get("message_count", 0) == 0:
        safe_print("⚠️ Slack API is configured but returned 0 messages. Check bot channel membership, scopes, and lookback window.")

# ---------------- FETCH DATA ----------------
def fetch_data():
    try:
        jira = JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))
    except Exception as exc:
        METRICS.record_external_api_failure("jira")
        log_event("error", "jira_auth_failed", error_type=type(exc).__name__, error_message=str(exc))
        raise RuntimeError("❌ Jira authentication failed")

    # ✅ FINAL FIX: REMOVE sprint filter (Team-managed limitation)
    jql = 'project = SHOP ORDER BY created DESC'

    safe_print("🔍 JQL:" + " " + jql)

    issues = jira.search_issues(
        jql,
        maxResults=50,
        fields=f"summary,status,issuetype,{STORY_POINTS_FIELD},labels,created,updated"
    )

    # --- GitHub ---
    gh = Github(auth=Auth.Token(GH_TOKEN))
    try:
        repo = gh.get_repo(GH_REPO)
    except BadCredentialsException:
        METRICS.record_external_api_failure("github")
        log_event("error", "github_auth_failed", error_type="BadCredentialsException")
        raise RuntimeError("❌ GitHub authentication failed")

    try:
        pulls = list(islice(repo.get_pulls(state='all'), 50))
    except Exception as exc:
        METRICS.record_external_api_failure("github")
        log_event("error", "github_pull_fetch_failed", error_type=type(exc).__name__, error_message=str(exc))
        raise RuntimeError("❌ GitHub pull request fetch failed")

    return issues, pulls, repo


def _latest_deployment_state(repo, environment_name):
    try:
        deployments = repo.get_deployments(environment=environment_name)
        latest_deployment = next(iter(deployments), None)

        if not latest_deployment:
            return "not_found"

        latest_status = next(iter(latest_deployment.get_statuses()), None)
        if latest_status:
            return latest_status.state.lower()

        return "created"
    except Exception:
        return "unknown"


def process_cicd(repo):
    try:
        workflow_runs = list(islice(repo.get_workflow_runs(), 50))
    except Exception as exc:
        METRICS.record_external_api_failure("github")
        log_event("warning", "github_cicd_fetch_failed", error_type=type(exc).__name__, error_message=str(exc))
        workflow_runs = []

    build_failures = sum(
        1
        for run in workflow_runs
        if (run.conclusion or "").lower() == "failure"
    )

    if workflow_runs:
        last_run = workflow_runs[0]
        last_build = (last_run.conclusion or last_run.status or "unknown").lower()
    else:
        last_build = "unknown"

    return {
        "build_failures": build_failures,
        "last_build": last_build,
        "environments": {
            "uat": _latest_deployment_state(repo, "uat"),
            "prod": _latest_deployment_state(repo, "prod"),
        },
    }


def build_health_signals(jira_summary, github_summary, cicd_summary):
    signals = []

    def add_signal(text):
        signals.append(text)

    if cicd_summary.get("build_failures", 0) > 0:
        add_signal(f"{cicd_summary['build_failures']} build failures detected")

    if cicd_summary.get("last_build") in {"failure", "failed", "cancelled"}:
        add_signal(f"Last build status: {cicd_summary['last_build']}")

    prod_state = cicd_summary.get("environments", {}).get("prod", "unknown")
    if prod_state in {"failure", "error", "inactive", "unknown", "not_found"}:
        add_signal(f"Deployment risk in production (prod status: {prod_state})")

    blocked_details = jira_summary.get("blocked_details", [])
    blocked_over_4d = sum(1 for item in blocked_details if item.get("over_4d_by_hours", 0) > 0)
    if blocked_over_4d > 0:
        add_signal(f"{blocked_over_4d} stories blocked > 4 days")
    elif jira_summary.get("blocked", 0) > 0:
        add_signal(f"{jira_summary['blocked']} stories blocked")

    pending_review_over_48h = github_summary.get("pending_review_over_48h", 0)
    if pending_review_over_48h > 0:
        add_signal(f"{pending_review_over_48h} PRs pending review > 48 hrs")
    elif github_summary.get("pending_reviews", 0) > 0:
        add_signal(f"{github_summary['pending_reviews']} PRs pending review")

    coverage = github_summary.get("test_coverage_pct")
    if coverage is not None:
        add_signal(f"Test coverage at {coverage}%")

    if not signals:
        add_signal("No major delivery risks detected from current snapshots")

    return signals


def build_recommendations(jira_summary, github_summary, cicd_summary):
    recommendations = []

    blocked_details = jira_summary.get("blocked_details", [])
    blocked_over_4d = [item for item in blocked_details if (item.get("over_4d_by_hours") or 0) > 0]
    if blocked_over_4d:
        issue_ids = ", ".join(item.get("id", "") for item in blocked_over_4d[:3] if item.get("id"))
        recommendations.append(f"Escalate blocked stories older than 4 days ({issue_ids}) and assign owners for same-day unblock.")
    elif jira_summary.get("blocked", 0) > 0:
        recommendations.append("Run a blocker triage with dev + QA today and convert each blocker into a tracked action item.")

    if github_summary.get("pending_review_over_48h", 0) > 0:
        recommendations.append("Create a review SLA lane for PRs older than 48 hours and clear the queue before new feature pickup.")
    elif github_summary.get("pending_reviews", 0) > 0:
        recommendations.append("Timebox two review windows per day to reduce pending PRs and avoid merge bottlenecks.")

    if cicd_summary.get("build_failures", 0) > 0 or cicd_summary.get("last_build") in {"failure", "failed", "cancelled"}:
        recommendations.append("Stabilize CI first: fix the top failing test/build step and enforce green build before merge.")

    prod_state = cicd_summary.get("environments", {}).get("prod", "unknown")
    if prod_state in {"failure", "error", "inactive", "unknown", "not_found"}:
        recommendations.append("Add a production deployment smoke-check gate and verify rollback readiness before next release.")

    if jira_summary.get("sprint_progress_pct", 0) < 50:
        recommendations.append("Re-scope sprint backlog to must-have items only and freeze low-priority work for this sprint.")

    while len(recommendations) < 3:
        recommendations.append("Track daily delivery health in standup using blockers, pending reviews, and build status trends.")

    return recommendations[:3]


def build_aggregated_report(jira_summary, github_summary, cicd_summary):
    return {
        "jira": jira_summary,
        "github": github_summary,
        "cicd": cicd_summary,
        "signals": build_health_signals(jira_summary, github_summary, cicd_summary),
    }

# ---------------- PROCESS DATA ----------------
def process_sprint(issues, pulls):
    total_sp = 0
    done_sp = 0
    completed_tasks = 0
    blocked_tasks = 0
    total_review_comments = 0
    pending_reviews = 0
    status_counts = Counter()
    canonical_status_counts = Counter()
    type_counts = Counter()
    created_last_7d = 0
    updated_last_7d = 0
    blocked_details = []
    sprint_summary = []
    pr_map = {}

    now_utc = datetime.now(timezone.utc)

    def parse_dt(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # --- Map PRs ---
    for pr in pulls:
        match = re.search(r'[A-Z]+-\d+', pr.title)
        if match:
            pr_map[match.group()] = pr.state

        # Open PRs are treated as pending review/action.
        if pr.state == "open":
            pending_reviews += 1

            created_at = getattr(pr, "created_at", None)
            created_dt = parse_dt(created_at)
            if created_dt and (now_utc - created_dt).days >= 2:
                github_summary_pending_age = True

        total_review_comments += pr.review_comments

    # --- Process Issues ---
    for issue in issues:
        fields = issue.fields

        # ✅ Robust Story Points extraction
        sp = getattr(fields, STORY_POINTS_FIELD, None)

        if sp is None:
            for attr in dir(fields):
                if "customfield" in attr:
                    val = getattr(fields, attr)
                    if isinstance(val, (int, float)):
                        sp = val
                        break

        sp = sp or 0

        status = fields.status.name
        canonical_status = _normalize_jira_status(status)
        status_category = getattr(getattr(fields, "status", None), "statusCategory", None)
        status_category_name = (getattr(status_category, "name", "") or "").lower()
        issue_type = getattr(getattr(fields, "issuetype", None), "name", "Unknown")
        labels = getattr(fields, "labels", [])
        updated_at = parse_dt(getattr(fields, "updated", None))
        created_at = parse_dt(getattr(fields, "created", None))

        status_counts[status] += 1
        canonical_status_counts[canonical_status] += 1
        type_counts[issue_type] += 1
        if created_at and (now_utc - created_at).days <= 7:
            created_last_7d += 1
        if updated_at and (now_utc - updated_at).days <= 7:
            updated_last_7d += 1

        total_sp += sp

        # Use status category first because workflow names differ across Jira boards.
        is_done = status_category_name == "done" or status.lower() in ['done', 'closed', 'resolved']

        if is_done:
            done_sp += sp
            completed_tasks += 1

        if status.lower() == 'blocked' or "blocked" in [l.lower() for l in labels]:
            blocked_tasks += 1
            blocked_for_hours = None
            over_4d_by_hours = None
            remaining_to_4d_hours = None

            if updated_at:
                blocked_for_hours = round((now_utc - updated_at).total_seconds() / 3600, 1)
                over_4d_by_hours = round(max(0.0, blocked_for_hours - 96.0), 1)
                remaining_to_4d_hours = round(max(0.0, 96.0 - blocked_for_hours), 1)

            blocked_details.append({
                "id": issue.key,
                "status": status,
                "blocked_for_hours": blocked_for_hours,
                "over_4d_by_hours": over_4d_by_hours,
                "remaining_to_4d_hours": remaining_to_4d_hours,
            })

        sprint_summary.append({
            "id": issue.key,
            "status": status,
            "points": sp,
            "blocked": "blocked" in [l.lower() for l in labels],
            "has_pr": issue.key in pr_map,
            "pr_state": pr_map.get(issue.key, "none")
        })

    total_tasks = len(issues)
    task_progress = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    # If story points are unavailable, use task completion so sprint completion isn't forced to 0.
    completion = (done_sp / total_sp * 100) if total_sp > 0 else task_progress

    jira_summary = {
        "sprint_progress_pct": round(task_progress, 2),
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "blocked": blocked_tasks,
        "blocked_details": blocked_details,
        "issue_count": total_tasks,
        "status_counts": dict(status_counts),
        "canonical_status_counts": {label: canonical_status_counts.get(label, 0) for label in JIRA_STATUS_ORDER},
        "type_counts": dict(type_counts),
        "activity": {
            "created_last_7d": created_last_7d,
            "updated_last_7d": updated_last_7d,
            "open_items": max(0, total_tasks - completed_tasks - blocked_tasks),
        },
    }

    github_summary = {
        "total_prs": len(pulls),
        "pending_reviews": pending_reviews,
        "review_comments": total_review_comments,
        "pending_review_over_48h": sum(
            1
            for pr in pulls
            if pr.state == "open"
            and parse_dt(getattr(pr, "created_at", None))
            and (now_utc - parse_dt(getattr(pr, "created_at", None))).total_seconds() >= 48 * 3600
        ),
        "test_coverage_pct": None,
    }

    return completion, sprint_summary, jira_summary, github_summary

# ---------------- GROQ AI ----------------
def get_groq_insights(completion_pct, summary, aggregated_report, slack_report):
    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)

    prompt = f"""
Sprint Completion: {completion_pct:.2f}%

Issue/Story Data:
{summary}

Aggregated Delivery Report (Jira + GitHub + CI/CD):
{json.dumps(aggregated_report, indent=2)}

Slack Conversation Analysis:
{json.dumps(slack_report, indent=2)}

Give:
1. 3+ key risks
2. 3+ recommendations
3. 1 short weekly Slack summary covering issues, wins, and blockers

Rules:
- If build_failures > 0, include build stability risk.
- If prod environment is not success, include deployment risk.
- If pending_reviews > 0, include code review bottleneck risk.
- Use Slack conversation analysis only for the report narrative, not for sprint completion or sprint scoring.

Format:
Risks detected:
• <risk 1>
• <risk 2>
• <risk 3>
• <risk 4 if available>

Recommendations:
• <recommendation 1>
• <recommendation 2>
• <recommendation 3>

Weekly Slack summary:
• <one short paragraph>

Keep it short and professional.
"""

    response = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
    )

    return response.choices[0].message.content


class ObserveAgent:
    @staticmethod
    def run():
        issues, pulls, repo = fetch_data()
        slack_messages, slack_fetch_diagnostics = fetch_slack_messages()
        cicd_summary = process_cicd(repo)
        return {
            "issues": issues,
            "pulls": pulls,
            "repo": repo,
            "slack_messages": slack_messages,
            "slack_fetch_diagnostics": slack_fetch_diagnostics,
            "cicd_summary": cicd_summary
        }

class AnalyzerAgent:
    @staticmethod
    def run(observations, include_ai_insights, runtime):
        completion, summary, jira_summary, github_summary = process_sprint(
            observations["issues"], observations["pulls"]
        )
        slack_summary = analyze_slack_messages(
            observations["slack_messages"], observations["slack_fetch_diagnostics"]
        )
        compact_slack_summary = build_compact_slack_summary(slack_summary)
        signals = build_health_signals(jira_summary, github_summary, observations["cicd_summary"])
        recommendations = build_recommendations(jira_summary, github_summary, observations["cicd_summary"])
        charts = build_report_charts(jira_summary)

        final_report = {
            "jira": jira_summary,
            "github": github_summary,
            "cicd": observations["cicd_summary"],
            "slack": compact_slack_summary,
            "signals": signals,
            "recommendations": recommendations,
            "charts": charts,
            "sprint_completion_pct": round(completion, 2),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
        }

        insights = None
        if include_ai_insights:
            if not runtime.get("supported"):
                insights = (
                    "AI insights skipped due to Python runtime compatibility. "
                    f"Current={runtime.get('current')}, recommended={runtime.get('recommended')}."
                )
                log_event("warning", "groq_skipped_unsupported_python", runtime=runtime)
            else:
                try:
                    insights = get_groq_insights(completion, summary, final_report, compact_slack_summary)
                except Exception as exc:
                    METRICS.record_external_api_failure("groq")
                    log_event("error", "groq_insights_failed", error_type=type(exc).__name__, error_message=str(exc))
                    insights = "AI insights unavailable for this run due to an upstream LLM/API error."

        return {
            "report": final_report,
            "insights": insights
        }

class DecideAgent:
    @staticmethod
    def run(analysis):
        facts = _risk_facts(analysis["report"])
        memory_state = _load_memory_state()
        memory_feedback = _memory_feedback(memory_state)
        plan = run_agentic_planner(analysis["report"], memory_feedback=memory_feedback)
        
        return {
            "facts": facts,
            "plan": plan,
            "memory_state": memory_state,
            "memory_feedback": memory_feedback,
            "action_queue": plan.get("action_queue", [])
        }

class ActAgent:
    @staticmethod
    def run(decisions, execute_enabled=False):
        run_id = datetime.now(timezone.utc).strftime("agent-%Y%m%d-%H%M%S")
        action_queue = decisions["action_queue"]
        executed_actions = execute_actions(action_queue, run_id=run_id, execute_enabled=execute_enabled)
        return {
            "run_id": run_id,
            "executed_actions": executed_actions
        }

class LearnAgent:
    @staticmethod
    def run(decisions, executions, execute_enabled=False):
        memory_state = decisions["memory_state"]
        facts = decisions["facts"]
        action_queue = decisions["action_queue"]
        executed_actions = executions["executed_actions"]
        run_id = executions["run_id"]
        
        previous_executed_run = _select_previous_executed_run(memory_state)
        trend_baseline_run, trend_comparison_basis = _select_trend_baseline_run(memory_state)
        trend_previous_facts = trend_baseline_run.get("observed_facts", {}) if trend_baseline_run else {}
        
        outcome_tracking = _evaluate_action_outcomes(previous_executed_run, facts)
        escalation_actions = _derive_escalation_actions(outcome_tracking)
        if escalation_actions:
            combined = action_queue + escalation_actions
            action_queue = _sort_actions_by_priority(combined)

        trend = _trend_analysis(
            trend_previous_facts,
            facts,
            comparison_basis=trend_comparison_basis,
        )
        
        status_counts = {
            "executed": sum(1 for item in executed_actions if item.get("status") == "executed"),
            "dry_run": sum(1 for item in executed_actions if item.get("status") == "dry_run"),
            "skipped": sum(1 for item in executed_actions if item.get("status") == "skipped"),
            "failed": sum(1 for item in executed_actions if item.get("status") == "failed"),
        }

        outcome_status_counts = {
            "resolved": sum(1 for item in outcome_tracking if item.get("status") == "resolved"),
            "improving": sum(1 for item in outcome_tracking if item.get("status") == "improving"),
            "pending_observation": sum(1 for item in outcome_tracking if item.get("status") == "pending_observation"),
            "unresolved": sum(1 for item in outcome_tracking if item.get("status") == "unresolved"),
            "regressed": sum(1 for item in outcome_tracking if item.get("status") == "regressed"),
        }
        
        run_record = {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "execute_enabled": execute_enabled,
            "observed_facts": facts,
            "trend": trend,
            "action_ids": [item.get("action_id") for item in action_queue],
            "status_counts": status_counts,
            "executed_actions": executed_actions,
            "evaluated_previous_outcomes": outcome_tracking,
        }

        runs = memory_state.get("runs", [])
        runs.append(run_record)
        max_runs = _memory_max_runs()
        memory_state["runs"] = runs[-max_runs:]
        _save_memory_state(memory_state)

        next_focus = "Improve routing of actions to owners and add result feedback ingestion."
        if status_counts["failed"] > 0:
            next_focus = "Execution failures detected. Prioritize credential and endpoint reliability fixes."
        elif status_counts["skipped"] > 0:
            next_focus = "Execution skipped for some actions. Configure AGENT_ALERT_WEBHOOK_URL to enable delivery."
        elif status_counts["executed"] > 0:
            next_focus = "Execution succeeded. Add business outcome feedback loop to measure action impact next run."

        if trend.get("has_baseline") and trend.get("completion_delta", 0) > 0:
            next_focus = "Delivery trend is improving. Keep current interventions and monitor for regression."
        if outcome_status_counts["unresolved"] > 0 or outcome_status_counts["regressed"] > 0:
            next_focus = "Some prior actions did not resolve issues. Re-plan with stronger interventions and owners."
        if escalation_actions:
            next_focus = "Auto-escalation triggered for unresolved outcomes older than 24h. Monitor escalation ticket and stakeholder response."

        return {
            "enabled": True,
            "mode": "observe-analyze-decide-act-learn",
            "run_id": run_id,
            "plan": decisions["plan"],
            "action_queue": action_queue,
            "execution": {
                "enabled": execute_enabled,
                "executed_actions": executed_actions,
                "status_counts": status_counts,
            },
            "tracking": {
                "actions_total": len(action_queue),
                "actions_executed": status_counts["executed"],
                "actions_failed": status_counts["failed"],
                "outcome_tracking": outcome_tracking,
                "outcome_status_counts": outcome_status_counts,
                "escalations_triggered": len(escalation_actions),
                "escalation_action_ids": [item.get("action_id") for item in escalation_actions],
            },
            "memory": {
                "file": _memory_file_path(),
                "runs_stored": len(memory_state.get("runs", [])),
                "last_5_status_totals": decisions["memory_feedback"].get("status_totals_last_5", {}),
                "recurring_failed_action_ids": decisions["memory_feedback"].get("recurring_failed_action_ids", []),
                "trend": trend,
            },
            "learning": {
                "performance_summary": trend.get("performance_summary"),
                "resolved_problems": trend.get("resolved_problems", []),
                "next_focus": next_focus,
            },
        }

def generate_weekly_report(include_ai_insights=True, agent_mode=False, agent_execute=False):
    validate_config()
    runtime = runtime_support_status()

    # Flow
    observations = ObserveAgent.run()
    analysis = AnalyzerAgent.run(observations, include_ai_insights, runtime)
    
    agent = None
    if agent_mode:
        decisions = DecideAgent.run(analysis)
        executions = ActAgent.run(decisions, execute_enabled=agent_execute)
        agent = LearnAgent.run(decisions, executions, execute_enabled=agent_execute)
        
        # Merge back into report structure for backwards compatibility
        analysis["report"]["executed_actions"] = executions["executed_actions"]

    return {
        "report": analysis["report"],
        "insights": analysis["insights"],
        "agent": agent,
    }

# ---------------- MAIN ----------------
if __name__ == "__main__":
    try:
        validate_config()

        print_slack_config_warning()

        safe_print("🛰️ Connecting to APIs...")
        issues, pulls, repo = fetch_data()

        safe_print(f"✅ Jira issues: {len(issues)} | GitHub PRs: {len(pulls)}")

        if not issues:
            safe_print("⚠️ Still no issues → check project key or permissions")

        safe_print("🧬 Processing data...")
        report_output = generate_weekly_report(include_ai_insights=True)
        final_report = report_output["report"]
        insights = report_output["insights"] or "No AI insights available."

        safe_print("\n📦 Jira Sprint Summary:")
        print(json.dumps(final_report["jira"], indent=2))

        safe_print("\n📦 GitHub Summary:")
        print(json.dumps(final_report["github"], indent=2))

        safe_print("\n📦 Slack Summary:")
        print(json.dumps(final_report["slack"], indent=2))

        safe_print("\n📦 CI/CD Summary:")
        print(json.dumps(final_report["cicd"], indent=2))

        safe_print("\n📦 Signals:")
        print(json.dumps(final_report["signals"], indent=2))

        print("\n" + "-" * 50)
        safe_print(f"📊 SPRINT COMPLETION: {final_report['sprint_completion_pct']:.2f}%")
        print("-" * 50)
        print(insights)

    except Exception as e:
        safe_print(f"❌ Error: {e}")
        sys.exit(1)