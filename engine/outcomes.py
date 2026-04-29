# -*- coding: utf-8 -*-
"""
engine/outcomes.py
~~~~~~~~~~~~~~~~~~
Outcome evaluation: compares the current sprint facts against the
previous run's facts to determine if past actions resolved their issues.
Also generates auto-escalation actions for unresolved outcomes.
"""

from datetime import datetime, timezone

from core.utils import safe_float, safe_int, parse_dt
from engine.memory import _is_prod_risky


# ---------------------------------------------------------------------------
# Per-metric evaluators
# ---------------------------------------------------------------------------

def _evaluate_numeric_outcome(
    action_id: str,
    prev_facts: dict,
    current_facts: dict,
    metric_key: str,
    label: str,
    higher_is_better: bool = False,
) -> dict:
    prev_value = safe_float(prev_facts.get(metric_key), 0.0)
    current_value = safe_float(current_facts.get(metric_key), 0.0)

    if higher_is_better:
        delta = round(current_value - prev_value, 2)
        status = "resolved" if delta >= 5 else "improving" if delta > 0 else "unresolved" if delta == 0 else "regressed"
    else:
        delta = round(prev_value - current_value, 2)
        if current_value == 0 and prev_value > 0:
            status = "resolved"
        elif delta > 0:
            status = "improving"
        elif delta == 0:
            status = "unresolved"
        else:
            status = "regressed"

    return {
        "action_id": action_id,
        "metric": metric_key,
        "status": status,
        "evidence": f"{label} changed from {prev_value:g} to {current_value:g}.",
        "previous": prev_value,
        "current": current_value,
        "delta": delta,
    }


def _evaluate_state_outcome(
    action_id: str,
    prev_facts: dict,
    current_facts: dict,
    metric_key: str,
    label: str,
) -> dict:
    previous_state = str(prev_facts.get(metric_key, "unknown"))
    current_state = str(current_facts.get(metric_key, "unknown"))
    prev_risky = _is_prod_risky(previous_state)
    curr_risky = _is_prod_risky(current_state)

    if prev_risky and not curr_risky:
        status = "resolved"
    elif prev_risky and curr_risky:
        status = "unresolved"
    elif (not prev_risky) and curr_risky:
        status = "regressed"
    else:
        status = "stable"

    return {
        "action_id": action_id,
        "metric": metric_key,
        "status": status,
        "evidence": f"{label} changed from '{previous_state}' to '{current_state}'.",
        "previous": previous_state,
        "current": current_state,
    }


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------

_NUMERIC_METRIC_MAP: dict = {
    "review-sla-001": ("pending_review_over_48h", "PR backlog >48h", False),
    "review-window-001": ("pending_review_over_48h", "PR backlog >48h", False),
    "blocker-escalation-001": ("blocked_over_4d", "Stories blocked >4 days", False),
    "blocker-triage-001": ("blocked_total", "Total blocked stories", False),
    "ci-stabilize-001": ("build_failures", "Build failures", False),
    "slack-alert-build-failure-001": ("build_failures", "Build failures", False),
    "scope-control-001": ("sprint_completion_pct", "Sprint completion", True),
}

_STATE_METRIC_MAP: dict = {
    "prod-readiness-001": ("prod_state", "PROD deployment state"),
    "slack-alert-prod-failure-001": ("prod_state", "PROD deployment state"),
    "slack-alert-uat-failure-001": ("uat_state", "UAT deployment state"),
}


def _evaluate_action_outcomes(previous_run, current_facts: dict) -> list:
    if not previous_run:
        return []

    previous_facts = previous_run.get("observed_facts", {})
    previous_ts = parse_dt(previous_run.get("timestamp_utc"))
    hours_since_previous = None
    if previous_ts:
        hours_since_previous = round(
            (datetime.now(timezone.utc) - previous_ts).total_seconds() / 3600, 1
        )

    executed_ids = [
        item.get("action_id")
        for item in previous_run.get("executed_actions", [])
        if item.get("status") == "executed" and item.get("action_id")
    ]

    outcomes = []
    for action_id in sorted(set(executed_ids)):
        if action_id in _NUMERIC_METRIC_MAP:
            metric_key, label, higher = _NUMERIC_METRIC_MAP[action_id]
            outcome = _evaluate_numeric_outcome(
                action_id, previous_facts, current_facts, metric_key, label, higher
            )
        elif action_id in _STATE_METRIC_MAP:
            metric_key, label = _STATE_METRIC_MAP[action_id]
            outcome = _evaluate_state_outcome(
                action_id, previous_facts, current_facts, metric_key, label
            )
        else:
            outcome = {
                "action_id": action_id,
                "metric": "generic",
                "status": "not_evaluable",
                "evidence": "No metric mapping defined for this action yet.",
            }

        if hours_since_previous is not None:
            outcome["hours_since_action"] = hours_since_previous
            if outcome.get("status") == "unresolved" and hours_since_previous < 24:
                outcome["status"] = "pending_observation"
                outcome["evidence"] += " Less than 24h since action execution."

        outcomes.append(outcome)

    return outcomes


def _derive_escalation_actions(outcome_tracking: list) -> list:
    """Auto-generate escalation actions for outcomes still unresolved after 24h."""
    escalation_actions = []
    seen: set = set()

    for outcome in outcome_tracking:
        status = outcome.get("status")
        age_hours = safe_float(outcome.get("hours_since_action"), 0.0)
        original_action_id = outcome.get("action_id", "unknown-action")

        if status not in {"unresolved", "regressed"}:
            continue
        if age_hours < 24:
            continue
        if original_action_id in seen:
            continue

        seen.add(original_action_id)
        escalation_actions.append({
            "action_id": f"auto-escalate-{original_action_id}",
            "priority": "P0",
            "owner": "Senior Stakeholders",
            "timebox_hours": 0.5,
            "objective": f"Escalate unresolved outcome for {original_action_id}",
            "reason": "Previous action remained unresolved/regressed after 24h.",
            "success_criteria": "Escalation ticket created and stakeholders notified.",
            "execute_message": f"Escalation needed: {original_action_id} is {status} after {age_hours}h.",
            "alert_type": "auto_escalation",
            "escalation_action": True,
            "original_action_id": original_action_id,
            "outcome_evidence": outcome.get("evidence", "No evidence"),
            "hours_since_action": age_hours,
        })

    return escalation_actions
