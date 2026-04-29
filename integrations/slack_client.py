# -*- coding: utf-8 -*-
"""
integrations/slack_client.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Slack API client: fetches messages, analyses keywords, and builds
the compact summary consumed by the rest of the pipeline.
"""

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from core.config import (
    SLACK_BOT_TOKEN,
    SLACK_CHANNEL_IDS,
    SLACK_KEYWORD_GROUPS,
    SLACK_LOOKBACK_DAYS,
    SLACK_MESSAGE_LIMIT,
)
from core.logging import METRICS, log_event
from core.utils import safe_print


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slack_config_mode() -> str:
    if SLACK_BOT_TOKEN and SLACK_CHANNEL_IDS.strip():
        return "slack_api"
    return "not_configured"


def _slack_channel_name(channel_id: str) -> str:
    return str(channel_id).strip() or "unknown" if channel_id else "unknown"


def _slack_date_bucket(message: dict) -> str:
    ts = message.get("ts")
    if ts is None:
        return "unknown-date"
    try:
        parsed = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return parsed.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "unknown-date"


def _fetch_slack_channel_history(token: str, channel_id: str, oldest_ts: int, limit: int) -> list:
    params = {"channel": channel_id, "limit": str(limit)}
    if oldest_ts:
        params["oldest"] = str(oldest_ts)

    url = "https://slack.com/api/conversations.history?" + urllib_parse.urlencode(params)
    req = urllib_request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    import json
    with urllib_request.urlopen(req, timeout=20) as response:
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_slack_messages() -> tuple[list, list]:
    """Return (messages, fetch_diagnostics). Both are empty lists when Slack is not configured."""
    if not (SLACK_BOT_TOKEN and SLACK_CHANNEL_IDS.strip()):
        return [], []

    oldest_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=SLACK_LOOKBACK_DAYS)).timestamp()
    )
    messages = []
    fetch_diagnostics = []

    for channel_id in [c.strip() for c in SLACK_CHANNEL_IDS.split(",") if c.strip()]:
        try:
            channel_messages = _fetch_slack_channel_history(
                SLACK_BOT_TOKEN, channel_id, oldest_ts, SLACK_MESSAGE_LIMIT
            )
            messages.extend(channel_messages)
            fetch_diagnostics.append(
                {"channel": channel_id, "status": "ok", "fetched_messages": len(channel_messages), "error": None}
            )
        except RuntimeError as exc:
            METRICS.record_external_api_failure("slack")
            log_event("warning", "slack_fetch_failed", channel=channel_id, error_message=str(exc))
            fetch_diagnostics.append(
                {"channel": channel_id, "status": "error", "fetched_messages": 0, "error": str(exc)}
            )
        except (urllib_error.URLError, urllib_error.HTTPError) as exc:
            METRICS.record_external_api_failure("slack")
            log_event("warning", "slack_fetch_http_failed", channel=channel_id, error_type=type(exc).__name__, error_message=str(exc))
            fetch_diagnostics.append(
                {"channel": channel_id, "status": "error", "fetched_messages": 0, "error": str(exc)}
            )

    return messages, fetch_diagnostics


def build_slack_week_summary(analyzed_messages: list, total_messages: int, category_counter: Counter) -> str:
    if not analyzed_messages:
        if total_messages == 0:
            if _slack_config_mode() == "slack_api":
                return "Slack API is configured, but no messages were retrieved in the selected lookback window."
            return "No Slack conversation data was provided for this report cycle."
        return "Slack data was received, but no delivery-related keywords were matched in the selected conversation window."

    issue_count = category_counter.get("issues", 0)
    success_count = category_counter.get("successes", 0)
    risk_count = category_counter.get("delivery_risks", 0)

    top_issue_texts = [i["text"] for i in analyzed_messages if "issues" in i["matched_categories"]][:3]
    top_success_texts = [i["text"] for i in analyzed_messages if "successes" in i["matched_categories"]][:3]
    top_risk_texts = [i["text"] for i in analyzed_messages if "delivery_risks" in i["matched_categories"]][:3]

    parts = [
        f"Reviewed {total_messages} Slack messages for the week.",
        f"Detected {issue_count} issue-related mentions, {success_count} success mentions, and {risk_count} delivery-risk mentions.",
    ]
    if top_issue_texts:
        parts.append("Main issue signals: " + " | ".join(top_issue_texts))
    if top_success_texts:
        parts.append("Notable wins: " + " | ".join(top_success_texts))
    if top_risk_texts:
        parts.append("Delivery risks: " + " | ".join(top_risk_texts))

    return " ".join(parts)


def build_slack_grouped_summary(analyzed_messages: list) -> dict:
    grouped_by_channel: dict = {}
    grouped_by_date: dict = {}

    for item in analyzed_messages:
        channel_key = _slack_channel_name(item.get("channel", "unknown"))
        date_key = _slack_date_bucket(item)
        grouped_by_channel.setdefault(channel_key, []).append(item)
        grouped_by_date.setdefault(date_key, []).append(item)

    def _summarise(groups):
        result = []
        for key, items in sorted(groups.items(), key=lambda e: (-len(e[1]), e[0])):
            cat = Counter()
            for i in items:
                cat.update(i.get("matched_categories", []))
            result.append({
                "channel" if "channel_key" not in str(key) else "channel": key,
                "matched_messages": len(items),
                "category_counts": dict(cat),
                "highlights": [i["text"] for i in items[:3]],
            })
        return result

    channel_summary = []
    for ch, items in sorted(grouped_by_channel.items(), key=lambda e: (-len(e[1]), e[0])):
        cat = Counter()
        for i in items:
            cat.update(i.get("matched_categories", []))
        channel_summary.append({
            "channel": ch,
            "matched_messages": len(items),
            "category_counts": dict(cat),
            "highlights": [i["text"] for i in items[:3]],
        })

    date_summary = []
    for dt, items in sorted(grouped_by_date.items(), key=lambda e: e[0], reverse=True):
        cat = Counter()
        for i in items:
            cat.update(i.get("matched_categories", []))
        date_summary.append({
            "date": dt,
            "matched_messages": len(items),
            "category_counts": dict(cat),
            "highlights": [i["text"] for i in items[:3]],
        })

    return {"by_channel": channel_summary, "by_date": date_summary}


def analyze_slack_messages(messages: list, fetch_diagnostics: list | None = None) -> dict:
    """Keyword-match messages and return a structured analysis dict."""
    fetch_diagnostics = fetch_diagnostics or []
    configured = _slack_config_mode() == "slack_api"

    patterns = {
        category: [re.compile(p, re.IGNORECASE) for p in pat_list]
        for category, pat_list in SLACK_KEYWORD_GROUPS.items()
    }

    analyzed_messages = []
    category_counter: Counter = Counter()
    keyword_counter: Counter = Counter()

    for message in messages:
        text = str(message.get("text", "")).strip()
        if not text:
            continue
        matched_categories = []
        matched_keywords = []
        for category, compiled in patterns.items():
            for cp in compiled:
                if cp.search(text):
                    matched_categories.append(category)
                    matched_keywords.append(cp.pattern)
                    category_counter[category] += 1
                    keyword_counter[cp.pattern] += 1
        if matched_categories:
            analyzed_messages.append({
                "text": text,
                "user": message.get("user", "unknown"),
                "channel": message.get("channel", "unknown"),
                "ts": message.get("ts"),
                "matched_categories": sorted(set(matched_categories)),
                "matched_keywords": matched_keywords,
            })

    issue_msgs = [i for i in analyzed_messages if "issues" in i["matched_categories"]]
    success_msgs = [i for i in analyzed_messages if "successes" in i["matched_categories"]]
    risk_msgs = [i for i in analyzed_messages if "delivery_risks" in i["matched_categories"]]
    failed_channels = [i for i in fetch_diagnostics if i.get("status") != "ok"]

    return {
        "enabled": configured,
        "source": _slack_config_mode(),
        "message_count": len(messages),
        "matched_message_count": len(analyzed_messages),
        "channels_requested": [i.get("channel") for i in fetch_diagnostics],
        "channels_failed": len(failed_channels),
        "fetch_diagnostics": fetch_diagnostics,
        "category_counts": dict(category_counter),
        "top_keywords": keyword_counter.most_common(8),
        "issue_messages": issue_msgs[:8],
        "success_messages": success_msgs[:8],
        "risk_messages": risk_msgs[:8],
        "week_summary": build_slack_week_summary(analyzed_messages, len(messages), category_counter),
        "grouped_summary": build_slack_grouped_summary(analyzed_messages),
    }


def build_compact_slack_summary(slack_summary: dict) -> dict:
    grouped = slack_summary.get("grouped_summary", {})
    by_channel = grouped.get("by_channel", [])
    by_date = grouped.get("by_date", [])

    return {
        "enabled": slack_summary.get("enabled", False),
        "source": slack_summary.get("source", "not_configured"),
        "message_count": slack_summary.get("message_count", 0),
        "matched_message_count": slack_summary.get("matched_message_count", 0),
        "channels_requested": slack_summary.get("channels_requested", []),
        "channels_failed": slack_summary.get("channels_failed", 0),
        "category_counts": slack_summary.get("category_counts", {}),
        "top_keywords": slack_summary.get("top_keywords", [])[:5],
        "issue_highlights": [i.get("text") for i in slack_summary.get("issue_messages", [])[:2]],
        "success_highlights": [i.get("text") for i in slack_summary.get("success_messages", [])[:2]],
        "risk_highlights": [i.get("text") for i in slack_summary.get("risk_messages", [])[:2]],
        "week_summary": slack_summary.get("week_summary", ""),
        "grouped_summary": {
            "by_channel": [
                {"channel": i.get("channel"), "matched_messages": i.get("matched_messages", 0), "category_counts": i.get("category_counts", {})}
                for i in by_channel[:3]
            ],
            "by_date": [
                {"date": i.get("date"), "matched_messages": i.get("matched_messages", 0), "category_counts": i.get("category_counts", {})}
                for i in by_date[:5]
            ],
        },
    }


def print_slack_config_warning() -> None:
    if _slack_config_mode() == "not_configured":
        safe_print(
            "⚠️ Slack is not configured. Add SLACK_BOT_TOKEN and SLACK_CHANNEL_IDS "
            "to include Slack conversation summaries."
        )


def print_slack_fetch_diagnostics(slack_summary: dict) -> None:
    if _slack_config_mode() != "slack_api":
        return
    channels = slack_summary.get("channels_requested", [])
    if channels:
        safe_print(f"🔌 Slack API channels configured: {', '.join(channels)}")
    failed = [i for i in slack_summary.get("fetch_diagnostics", []) if i.get("status") != "ok"]
    if failed:
        for item in failed:
            safe_print(f"⚠️ Slack fetch failed for channel {item.get('channel')}: {item.get('error')}")
        return
    if slack_summary.get("message_count", 0) == 0:
        safe_print(
            "⚠️ Slack API is configured but returned 0 messages. "
            "Check bot channel membership, scopes, and lookback window."
        )
