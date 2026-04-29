# -*- coding: utf-8 -*-
"""
core/config.py
~~~~~~~~~~~~~~
Centralised configuration: all environment variables, constants, and
startup validation live here. Every other module imports from this file.
"""

import os
import platform

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap — load .env before any other module reads os.getenv()
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# External API credentials
# ---------------------------------------------------------------------------
JIRA_SERVER: str = os.getenv("JIRA_SERVER", "")
JIRA_EMAIL: str = os.getenv("JIRA_EMAIL", "")
JIRA_TOKEN: str = os.getenv("JIRA_TOKEN", "")

GH_TOKEN: str = os.getenv("GH_TOKEN", "")
GH_REPO: str = os.getenv("GH_REPO", "")

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_FALLBACK_MODEL: str = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")

SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_IDS: str = os.getenv("SLACK_CHANNEL_IDS", "")

try:
    SLACK_LOOKBACK_DAYS: int = int(os.getenv("SLACK_LOOKBACK_DAYS", "7"))
except ValueError:
    SLACK_LOOKBACK_DAYS = 7

try:
    SLACK_MESSAGE_LIMIT: int = int(os.getenv("SLACK_MESSAGE_LIMIT", "250"))
except ValueError:
    SLACK_MESSAGE_LIMIT = 250

# ---------------------------------------------------------------------------
# Application constants
# ---------------------------------------------------------------------------
STORY_POINTS_FIELD: str = "customfield_10016"
LATEST_FULL_REPORT_FILE: str = "latest_full_report.json"

SUPPORTED_PYTHON_MIN: tuple = (3, 10)
SUPPORTED_PYTHON_MAX: tuple = (3, 14)

# Jira workflow ordering used by chart builders and status normalisation
JIRA_STATUS_ORDER: list = [
    "To Do",
    "Open",
    "Dev In Progress",
    "QA In Progress",
    "Peer Review",
    "Peer Accepted",
    "Completed",
    "Blocked",
]

JIRA_STATUS_ALIASES: dict = {
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
    "verified": "Completed",
    "accepted": "Completed",
    "blocked": "Blocked",
}

SLACK_KEYWORD_GROUPS: dict = {
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

# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def validate_config() -> None:
    """Raise RuntimeError if any required credential is missing."""
    required = {
        "JIRA_SERVER": JIRA_SERVER,
        "JIRA_EMAIL": JIRA_EMAIL,
        "JIRA_TOKEN": JIRA_TOKEN,
        "GH_TOKEN": GH_TOKEN,
        "GH_REPO": GH_REPO,
        "GROQ_API_KEY": GROQ_API_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(
            f"❌ Missing environment variables: {', '.join(missing)}. "
            "Check your .env file."
        )


def runtime_support_status() -> dict:
    """Return a dict describing whether the current Python version is supported."""
    import sys
    current = (sys.version_info.major, sys.version_info.minor)
    supported = SUPPORTED_PYTHON_MIN <= current <= SUPPORTED_PYTHON_MAX
    return {
        "supported": supported,
        "current": f"{current[0]}.{current[1]}",
        "recommended": "3.12",
        "supported_range": (
            f">={SUPPORTED_PYTHON_MIN[0]}.{SUPPORTED_PYTHON_MIN[1]} "
            f"and <={SUPPORTED_PYTHON_MAX[0]}.{SUPPORTED_PYTHON_MAX[1]}"
        ),
        "platform": platform.platform(),
    }
