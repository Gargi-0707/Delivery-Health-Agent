# -*- coding: utf-8 -*-
"""
engine/memory.py
~~~~~~~~~~~~~~~~
Persistent agent memory: load, save, and summarise run history
stored in agent_memory_history.json.
"""

import json
import os
from datetime import datetime, timezone

from core.utils import safe_int, safe_float, parse_dt


def _memory_file_path() -> str:
    raw_path = os.getenv("AGENT_MEMORY_FILE", "agent_memory_history.json").strip()
    if os.path.isabs(raw_path):
        return raw_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # memory file lives in the project root (one level above engine/)
    return os.path.join(base_dir, "..", raw_path)


def _memory_max_runs() -> int:
    return max(10, safe_int(os.getenv("AGENT_MEMORY_MAX_RUNS", "60"), 60))


def _load_memory_state() -> dict:
    path = _memory_file_path()
    if not os.path.exists(path):
        return {"schema_version": 1, "runs": []}

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "runs": []}

    if not isinstance(data, dict):
        return {"schema_version": 1, "runs": []}

    runs = data.get("runs", [])
    if not isinstance(runs, list):
        runs = []

    return {"schema_version": 1, "runs": runs}


def _save_memory_state(state: dict) -> None:
    path = _memory_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _memory_feedback(memory_state: dict) -> dict:
    runs = memory_state.get("runs", [])
    last_5 = runs[-5:]
    status_totals = {"executed": 0, "dry_run": 0, "skipped": 0, "failed": 0}
    skipped_due_to_webhook = 0
    failed_action_ids: dict = {}

    for run in last_5:
        counts = run.get("status_counts", {})
        for key in status_totals:
            status_totals[key] += safe_int(counts.get(key, 0), 0)

        for item in run.get("executed_actions", []):
            status = item.get("status")
            action_id = item.get("action_id", "unknown")
            detail = str(item.get("detail", "")).lower()
            if status == "failed":
                failed_action_ids[action_id] = failed_action_ids.get(action_id, 0) + 1
            if status == "skipped" and "webhook" in detail:
                skipped_due_to_webhook += 1

    recurring_failures = [aid for aid, count in failed_action_ids.items() if count >= 2]

    return {
        "runs_seen": len(runs),
        "status_totals_last_5": status_totals,
        "skipped_due_to_webhook_last_5": skipped_due_to_webhook,
        "recurring_failed_action_ids": recurring_failures,
    }


def _select_trend_baseline_run(memory_state: dict) -> tuple:
    runs = memory_state.get("runs") or []
    if not runs:
        return None, "baseline"

    now_date = datetime.now(timezone.utc).date()
    for run in reversed(runs):
        run_ts = parse_dt(run.get("timestamp_utc"))
        if run_ts and run_ts.date() < now_date:
            return run, "previous_day"

    return runs[-1], "last_run"


def _select_previous_executed_run(memory_state: dict):
    runs = memory_state.get("runs") or []
    if not runs:
        return None

    now_date = datetime.now(timezone.utc).date()

    for run in reversed(runs):
        executed = run.get("executed_actions", [])
        if not any(item.get("status") == "executed" for item in executed):
            continue
        run_ts = parse_dt(run.get("timestamp_utc"))
        if run_ts and run_ts.date() < now_date:
            return run

    for run in reversed(runs):
        if any(item.get("status") == "executed" for item in run.get("executed_actions", [])):
            return run

    return None


def _is_prod_risky(state) -> bool:
    return str(state or "unknown").lower() in {"failure", "error", "inactive", "unknown", "not_found"}


def _trend_analysis(previous_facts: dict, current_facts: dict, comparison_basis: str = "last_run") -> dict:
    if not previous_facts:
        current_completion = round(safe_float(current_facts.get("sprint_completion_pct"), 0.0), 2)
        return {
            "has_baseline": False,
            "last_completion": None,
            "current_completion": current_completion,
            "completion_delta": None,
            "comparison_basis": "baseline",
            "performance_summary": "Baseline created. Future runs will show completion trend and solved problems.",
            "resolved_problems": [],
        }

    last_completion = round(safe_float(previous_facts.get("sprint_completion_pct"), 0.0), 2)
    current_completion = round(safe_float(current_facts.get("sprint_completion_pct"), 0.0), 2)
    delta = round(current_completion - last_completion, 2)

    basis_label = "previous day" if comparison_basis == "previous_day" else "last run"
    if delta > 0:
        performance_summary = f"Performance improved by {delta:.2f}% compared to {basis_label}."
    elif delta < 0:
        performance_summary = f"Performance dropped by {abs(delta):.2f}% compared to {basis_label}."
    else:
        performance_summary = f"Performance is unchanged compared to {basis_label}."

    resolved_problems = []
    if safe_int(previous_facts.get("build_failures"), 0) > 0 and safe_int(current_facts.get("build_failures"), 0) == 0:
        resolved_problems.append("Build failures were cleared.")
    if safe_int(previous_facts.get("blocked_over_4d"), 0) > 0 and safe_int(current_facts.get("blocked_over_4d"), 0) == 0:
        resolved_problems.append("Stories blocked over 4 days were cleared.")
    if safe_int(previous_facts.get("pending_review_over_48h"), 0) > 0 and safe_int(current_facts.get("pending_review_over_48h"), 0) == 0:
        resolved_problems.append("PR review backlog older than 48 hours was cleared.")
    if _is_prod_risky(previous_facts.get("prod_state")) and not _is_prod_risky(current_facts.get("prod_state")):
        resolved_problems.append("Production deployment risk status improved.")
    if _is_prod_risky(previous_facts.get("uat_state")) and not _is_prod_risky(current_facts.get("uat_state")):
        resolved_problems.append("UAT deployment risk status improved.")

    return {
        "has_baseline": True,
        "last_completion": last_completion,
        "current_completion": current_completion,
        "completion_delta": delta,
        "comparison_basis": comparison_basis,
        "performance_summary": performance_summary,
        "resolved_problems": resolved_problems,
    }
