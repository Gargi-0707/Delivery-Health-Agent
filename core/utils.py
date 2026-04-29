# -*- coding: utf-8 -*-
"""
core/utils.py
~~~~~~~~~~~~~
Shared low-level helpers: safe type conversions, datetime parsing,
Jira status normalisation, and a Windows-safe print wrapper.
"""

import sys
from datetime import datetime, timezone

from core.config import JIRA_STATUS_ALIASES


# ---------------------------------------------------------------------------
# Safe-print wrapper
# Handles encoding issues on Windows when stdout is redirected to a file.
# ---------------------------------------------------------------------------

def safe_print(text: str = "") -> None:
    """Print *text* to stdout, replacing unencodable characters rather than crashing."""
    try:
        print(text)
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            fallback = str(text).encode("utf-8", "replace").decode("utf-8", "replace")
            print(fallback)
        except Exception:
            try:
                fallback = str(text).encode("ascii", "replace").decode("ascii")
                print(fallback, file=sys.stdout, flush=True)
            except Exception:
                pass  # Total silence if even this fails


# ---------------------------------------------------------------------------
# Type-safe conversion helpers
# ---------------------------------------------------------------------------

def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------

def parse_dt(value) -> datetime | None:
    """Parse an ISO-8601 string or datetime object into a timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Jira status helpers
# ---------------------------------------------------------------------------

def normalize_jira_status(status_name: str) -> str:
    """Map any Jira status string to its canonical form using JIRA_STATUS_ALIASES."""
    normalized = str(status_name or "").strip().lower()
    if not normalized:
        return "Open"

    if normalized in JIRA_STATUS_ALIASES:
        return JIRA_STATUS_ALIASES[normalized]

    for alias, canonical in JIRA_STATUS_ALIASES.items():
        if alias in normalized:
            return canonical

    return str(status_name).strip() or "Open"
