import json
import logging
import threading
import os
from datetime import datetime, timezone


_LOGGER = logging.getLogger("delivery_health")


def configure_structured_logging():
    if _LOGGER.handlers:
        return

    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def log_event(level, event, **fields):
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    message = json.dumps(payload, default=str)

    if level == "error":
        _LOGGER.error(message)
    elif level == "warning":
        _LOGGER.warning(message)
    else:
        _LOGGER.info(message)


def _safe_ratio(numerator, denominator):
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


class MetricsStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.total_runs = 0
        self.successful_runs = 0
        self.failed_runs = 0

        self.external_api_failures = {
            "jira": 0,
            "github": 0,
            "slack": 0,
            "groq": 0,
            "other": 0,
        }

        self.action_attempted = 0
        self.action_executed = 0

        self.unresolved_age_samples = []

    def record_run_success(self, output):
        with self._lock:
            self.total_runs += 1
            self.successful_runs += 1

            agent = (output or {}).get("agent") or {}
            execution = (agent.get("execution") or {})
            status_counts = execution.get("status_counts") or {}

            executed = int(status_counts.get("executed", 0) or 0)
            failed = int(status_counts.get("failed", 0) or 0)
            skipped = int(status_counts.get("skipped", 0) or 0)
            dry_run = int(status_counts.get("dry_run", 0) or 0)

            self.action_executed += executed
            self.action_attempted += executed + failed + skipped + dry_run

            tracking = (agent.get("tracking") or {}).get("outcome_tracking") or []
            for item in tracking:
                if item.get("status") not in {"unresolved", "regressed"}:
                    continue
                age = item.get("hours_since_action")
                if age is None:
                    continue
                try:
                    self.unresolved_age_samples.append(float(age))
                except (TypeError, ValueError):
                    continue

            if len(self.unresolved_age_samples) > 500:
                self.unresolved_age_samples = self.unresolved_age_samples[-500:]

    def record_run_failure(self):
        with self._lock:
            self.total_runs += 1
            self.failed_runs += 1

    def record_external_api_failure(self, api_name):
        normalized = str(api_name or "other").strip().lower()
        if normalized not in self.external_api_failures:
            normalized = "other"

        with self._lock:
            self.external_api_failures[normalized] += 1

    def snapshot(self):
        with self._lock:
            success_rate_pct = _safe_ratio(self.successful_runs, self.total_runs)
            action_execution_success_pct = _safe_ratio(self.action_executed, self.action_attempted)

            unresolved_avg_age = 0.0
            unresolved_max_age = 0.0
            if self.unresolved_age_samples:
                unresolved_avg_age = round(sum(self.unresolved_age_samples) / len(self.unresolved_age_samples), 2)
                unresolved_max_age = round(max(self.unresolved_age_samples), 2)

            return {
                "runs": {
                    "total": self.total_runs,
                    "successful": self.successful_runs,
                    "failed": self.failed_runs,
                    "success_rate_pct": success_rate_pct,
                },
                "external_api_failures": dict(self.external_api_failures),
                "action_execution": {
                    "attempted": self.action_attempted,
                    "executed": self.action_executed,
                    "success_pct": action_execution_success_pct,
                },
                "unresolved_outcome_aging_hours": {
                    "sample_count": len(self.unresolved_age_samples),
                    "avg_hours": unresolved_avg_age,
                    "max_hours": unresolved_max_age,
                },
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            }

    def bootstrap_from_memory_file(self, file_path):
        if not file_path or (not os.path.exists(file_path)):
            return False

        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False

        runs = payload.get("runs", []) if isinstance(payload, dict) else []
        if not isinstance(runs, list):
            return False

        with self._lock:
            self.total_runs = len(runs)
            self.successful_runs = len(runs)
            self.failed_runs = 0

            self.action_attempted = 0
            self.action_executed = 0
            self.unresolved_age_samples = []

            for run in runs:
                status_counts = run.get("status_counts", {}) if isinstance(run, dict) else {}
                executed = int(status_counts.get("executed", 0) or 0)
                failed = int(status_counts.get("failed", 0) or 0)
                skipped = int(status_counts.get("skipped", 0) or 0)
                dry_run = int(status_counts.get("dry_run", 0) or 0)

                self.action_executed += executed
                self.action_attempted += executed + failed + skipped + dry_run

                outcomes = run.get("evaluated_previous_outcomes", []) if isinstance(run, dict) else []
                if not isinstance(outcomes, list):
                    continue

                for item in outcomes:
                    if not isinstance(item, dict):
                        continue
                    if item.get("status") not in {"unresolved", "regressed"}:
                        continue
                    age = item.get("hours_since_action")
                    if age is None:
                        continue
                    try:
                        self.unresolved_age_samples.append(float(age))
                    except (TypeError, ValueError):
                        continue

            if len(self.unresolved_age_samples) > 500:
                self.unresolved_age_samples = self.unresolved_age_samples[-500:]

        return True


METRICS = MetricsStore()


def bootstrap_metrics_from_agent_memory():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    default_file = os.getenv("AGENT_MEMORY_FILE", "agent_memory_history.json").strip() or "agent_memory_history.json"
    path = default_file if os.path.isabs(default_file) else os.path.join(base_dir, default_file)
    loaded = METRICS.bootstrap_from_memory_file(path)
    if loaded:
        log_event("info", "metrics_bootstrapped", source=path)
    else:
        log_event("warning", "metrics_bootstrap_skipped", source=path)
