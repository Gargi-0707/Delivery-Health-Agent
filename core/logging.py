# -*- coding: utf-8 -*-
"""
core/logging.py
~~~~~~~~~~~~~~~
Structured logging, a thread-safe Metrics singleton, and the
bootstrap helper that pre-fills metrics from agent memory.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

_LOGGER = logging.getLogger("delivery_health")


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

def configure_structured_logging() -> None:
    """Attach a single StreamHandler to the delivery_health logger (idempotent)."""
    if _LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def log_event(level: str, event: str, **kwargs) -> None:
    """Emit a structured JSON log line at *level*."""
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **kwargs,
    }
    getattr(_LOGGER, level, _LOGGER.info)(json.dumps(payload))


# ---------------------------------------------------------------------------
# Thread-safe Metrics singleton
# ---------------------------------------------------------------------------

class _Metrics:
    """In-process counters for the health endpoint and observability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_runs: int = 0
        self.successful_runs: int = 0
        self.failed_runs: int = 0
        self.external_api_failures: dict = {}
        self.last_run_ts: str | None = None
        self.last_run_duration_ms: int | None = None
        self.last_sprint_completion_pct: float | None = None

    def record_run_success(self, output: dict) -> None:
        with self._lock:
            self.total_runs += 1
            self.successful_runs += 1
            self.last_run_ts = datetime.now(timezone.utc).isoformat()
            report = output.get("report", {})
            self.last_sprint_completion_pct = report.get("sprint_completion_pct")

    def record_run_failure(self) -> None:
        with self._lock:
            self.total_runs += 1
            self.failed_runs += 1

    def record_external_api_failure(self, service: str) -> None:
        with self._lock:
            self.external_api_failures[service] = (
                self.external_api_failures.get(service, 0) + 1
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_runs": self.total_runs,
                "successful_runs": self.successful_runs,
                "failed_runs": self.failed_runs,
                "external_api_failures": dict(self.external_api_failures),
                "last_run_ts": self.last_run_ts,
                "last_run_duration_ms": self.last_run_duration_ms,
                "last_sprint_completion_pct": self.last_sprint_completion_pct,
            }


# Module-level singleton — import this everywhere
METRICS = _Metrics()


# ---------------------------------------------------------------------------
# Bootstrap from persisted agent memory
# ---------------------------------------------------------------------------

def bootstrap_metrics_from_agent_memory() -> None:
    """
    On server startup, pre-fill METRICS from the most recent run in
    agent_memory_history.json so the /health endpoint is useful immediately.
    """
    memory_file = os.getenv("AGENT_MEMORY_FILE", "agent_memory_history.json").strip()
    if not os.path.isabs(memory_file):
        memory_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", memory_file)

    if not os.path.exists(memory_file):
        return

    try:
        with open(memory_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        runs = data.get("runs", [])
        if not runs:
            return
        latest = runs[-1]
        facts = latest.get("observed_facts", {})
        with METRICS._lock:
            METRICS.total_runs = len(runs)
            METRICS.successful_runs = len(runs)
            METRICS.last_run_ts = latest.get("timestamp_utc")
            METRICS.last_sprint_completion_pct = facts.get("sprint_completion_pct")
    except Exception:
        pass  # Non-fatal — metrics stay at defaults
