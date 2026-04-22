import json
import os
import base64
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default):
    try:
        return float(value)
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


def _memory_file_path():
    raw_path = os.getenv("AGENT_MEMORY_FILE", "agent_memory_history.json").strip()
    if os.path.isabs(raw_path):
        return raw_path

    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, raw_path)


def _memory_max_runs():
    return max(10, _safe_int(os.getenv("AGENT_MEMORY_MAX_RUNS", "60"), 60))


def _load_memory_state():
    path = _memory_file_path()
    if not os.path.exists(path):
        return {
            "schema_version": 1,
            "runs": [],
        }

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "runs": [],
        }

    if not isinstance(data, dict):
        return {
            "schema_version": 1,
            "runs": [],
        }

    runs = data.get("runs", [])
    if not isinstance(runs, list):
        runs = []

    return {
        "schema_version": 1,
        "runs": runs,
    }


def _save_memory_state(state):
    path = _memory_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def _memory_feedback(memory_state):
    runs = memory_state.get("runs", [])
    last_5 = runs[-5:]
    status_totals = {"executed": 0, "dry_run": 0, "skipped": 0, "failed": 0}
    skipped_due_to_webhook = 0
    failed_action_ids = {}

    for run in last_5:
        counts = run.get("status_counts", {})
        for key in status_totals:
            status_totals[key] += _safe_int(counts.get(key, 0), 0)

        for item in run.get("executed_actions", []):
            status = item.get("status")
            action_id = item.get("action_id", "unknown")
            detail = str(item.get("detail", "")).lower()

            if status == "failed":
                failed_action_ids[action_id] = failed_action_ids.get(action_id, 0) + 1
            if status == "skipped" and "webhook" in detail:
                skipped_due_to_webhook += 1

    recurring_failures = [
        action_id
        for action_id, count in failed_action_ids.items()
        if count >= 2
    ]

    return {
        "runs_seen": len(runs),
        "status_totals_last_5": status_totals,
        "skipped_due_to_webhook_last_5": skipped_due_to_webhook,
        "recurring_failed_action_ids": recurring_failures,
    }


def _is_prod_risky(state):
    return str(state or "unknown").lower() in {"failure", "error", "inactive", "unknown", "not_found"}


def _evaluate_numeric_outcome(action_id, prev_facts, current_facts, metric_key, label, higher_is_better=False):
    prev_value = _safe_float(prev_facts.get(metric_key), 0.0)
    current_value = _safe_float(current_facts.get(metric_key), 0.0)

    if higher_is_better:
        delta = round(current_value - prev_value, 2)
        if delta > 0:
            status = "resolved" if delta >= 5 else "improving"
        elif delta == 0:
            status = "unresolved"
        else:
            status = "regressed"
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


def _evaluate_state_outcome(action_id, prev_facts, current_facts, metric_key, label):
    previous_state = str(prev_facts.get(metric_key, "unknown"))
    current_state = str(current_facts.get(metric_key, "unknown"))
    previous_risky = _is_prod_risky(previous_state)
    current_risky = _is_prod_risky(current_state)

    if previous_risky and not current_risky:
        status = "resolved"
    elif previous_risky and current_risky:
        status = "unresolved"
    elif (not previous_risky) and current_risky:
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


def _evaluate_action_outcomes(previous_run, current_facts):
    if not previous_run:
        return []

    previous_facts = previous_run.get("observed_facts", {})
    previous_ts = _parse_dt(previous_run.get("timestamp_utc"))
    hours_since_previous = None
    if previous_ts:
        hours_since_previous = round((datetime.now(timezone.utc) - previous_ts).total_seconds() / 3600, 1)

    executed_previous_actions = [
        item.get("action_id")
        for item in previous_run.get("executed_actions", [])
        if item.get("status") == "executed" and item.get("action_id")
    ]

    outcomes = []
    for action_id in sorted(set(executed_previous_actions)):
        if action_id in {"review-sla-001", "review-window-001"}:
            outcome = _evaluate_numeric_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="pending_review_over_48h",
                label="PR backlog >48h",
            )
        elif action_id in {"blocker-escalation-001"}:
            outcome = _evaluate_numeric_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="blocked_over_4d",
                label="Stories blocked >4 days",
            )
        elif action_id in {"blocker-triage-001"}:
            outcome = _evaluate_numeric_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="blocked_total",
                label="Total blocked stories",
            )
        elif action_id in {"ci-stabilize-001", "slack-alert-build-failure-001"}:
            outcome = _evaluate_numeric_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="build_failures",
                label="Build failures",
            )
        elif action_id in {"prod-readiness-001", "slack-alert-prod-failure-001"}:
            outcome = _evaluate_state_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="prod_state",
                label="PROD deployment state",
            )
        elif action_id in {"slack-alert-uat-failure-001"}:
            outcome = _evaluate_state_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="uat_state",
                label="UAT deployment state",
            )
        elif action_id in {"scope-control-001"}:
            outcome = _evaluate_numeric_outcome(
                action_id,
                previous_facts,
                current_facts,
                metric_key="sprint_completion_pct",
                label="Sprint completion",
                higher_is_better=True,
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


def _derive_escalation_actions(outcome_tracking):
    escalation_actions = []
    seen = set()

    for outcome in outcome_tracking:
        status = outcome.get("status")
        age_hours = _safe_float(outcome.get("hours_since_action"), 0.0)
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


def _sort_actions_by_priority(actions):
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(actions, key=lambda item: priority_rank.get(item.get("priority", "P3"), 3))


def _select_trend_baseline_run(memory_state):
    runs = memory_state.get("runs") or []
    if not runs:
        return None, "baseline"

    now_date = datetime.now(timezone.utc).date()
    for run in reversed(runs):
        run_ts = _parse_dt(run.get("timestamp_utc"))
        if run_ts and run_ts.date() < now_date:
            return run, "previous_day"

    return runs[-1], "last_run"


def _select_previous_executed_run(memory_state):
    runs = memory_state.get("runs") or []
    if not runs:
        return None

    now_date = datetime.now(timezone.utc).date()

    # Prefer the most recent executed run from a previous day for clearer day-over-day outcomes.
    for run in reversed(runs):
        executed_actions = run.get("executed_actions", [])
        if not any(item.get("status") == "executed" for item in executed_actions):
            continue
        run_ts = _parse_dt(run.get("timestamp_utc"))
        if run_ts and run_ts.date() < now_date:
            return run

    # Fallback: latest executed run regardless of date.
    for run in reversed(runs):
        executed_actions = run.get("executed_actions", [])
        if any(item.get("status") == "executed" for item in executed_actions):
            return run

    return None


def _trend_analysis(previous_facts, current_facts, comparison_basis="last_run"):
    if not previous_facts:
        current_completion = round(_safe_float(current_facts.get("sprint_completion_pct"), 0.0), 2)
        return {
            "has_baseline": False,
            "last_completion": None,
            "current_completion": current_completion,
            "completion_delta": None,
            "comparison_basis": "baseline",
            "performance_summary": "Baseline created. Future runs will show completion trend and solved problems.",
            "resolved_problems": [],
        }

    last_completion = round(_safe_float(previous_facts.get("sprint_completion_pct"), 0.0), 2)
    current_completion = round(_safe_float(current_facts.get("sprint_completion_pct"), 0.0), 2)
    delta = round(current_completion - last_completion, 2)

    basis_label = "last run"
    if comparison_basis == "previous_day":
        basis_label = "previous day"

    if delta > 0:
        performance_summary = f"Performance improved by {delta:.2f}% compared to {basis_label}."
    elif delta < 0:
        performance_summary = f"Performance dropped by {abs(delta):.2f}% compared to {basis_label}."
    else:
        performance_summary = f"Performance is unchanged compared to {basis_label}."

    resolved_problems = []

    previous_build_failures = _safe_int(previous_facts.get("build_failures"), 0)
    current_build_failures = _safe_int(current_facts.get("build_failures"), 0)
    if previous_build_failures > 0 and current_build_failures == 0:
        resolved_problems.append("Build failures were cleared.")

    previous_blocked_over_4d = _safe_int(previous_facts.get("blocked_over_4d"), 0)
    current_blocked_over_4d = _safe_int(current_facts.get("blocked_over_4d"), 0)
    if previous_blocked_over_4d > 0 and current_blocked_over_4d == 0:
        resolved_problems.append("Stories blocked over 4 days were cleared.")

    previous_pr_delay = _safe_int(previous_facts.get("pending_review_over_48h"), 0)
    current_pr_delay = _safe_int(current_facts.get("pending_review_over_48h"), 0)
    if previous_pr_delay > 0 and current_pr_delay == 0:
        resolved_problems.append("PR review backlog older than 48 hours was cleared.")

    previous_prod = previous_facts.get("prod_state")
    current_prod = current_facts.get("prod_state")
    if _is_prod_risky(previous_prod) and not _is_prod_risky(current_prod):
        resolved_problems.append("Production deployment risk status improved.")

    previous_uat = previous_facts.get("uat_state")
    current_uat = current_facts.get("uat_state")
    if _is_prod_risky(previous_uat) and not _is_prod_risky(current_uat):
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


def _risk_facts(final_report):
    jira = final_report.get("jira", {})
    github = final_report.get("github", {})
    cicd = final_report.get("cicd", {})

    return {
        "blocked_over_4d": sum(
            1
            for item in jira.get("blocked_details", [])
            if (item.get("over_4d_by_hours") or 0) > 0
        ),
        "blocked_total": jira.get("blocked", 0),
        "pending_review_over_48h": github.get("pending_review_over_48h", 0),
        "pending_reviews": github.get("pending_reviews", 0),
        "build_failures": cicd.get("build_failures", 0),
        "last_build": cicd.get("last_build", "unknown"),
        "uat_state": cicd.get("environments", {}).get("uat", "unknown"),
        "prod_state": cicd.get("environments", {}).get("prod", "unknown"),
        "sprint_completion_pct": final_report.get("sprint_completion_pct", 0),
    }


def _decide_actions(facts, memory_feedback=None):
    actions = []
    memory_feedback = memory_feedback or {}

    if facts.get("build_failures", 0) > 0 or facts.get("last_build") in {"failure", "failed", "cancelled"}:
        actions.append({
            "action_id": "slack-alert-build-failure-001",
            "priority": "P0",
            "owner": "Engineering",
            "timebox_hours": 0.25,
            "objective": "Send Slack alert for build failure",
            "reason": "Build failure requires immediate team awareness.",
            "success_criteria": "Build failure alert sent with triage context.",
            "execute_message": "Build failure detected. Immediate triage required.",
            "alert_type": "build_failure",
        })
        actions.append({
            "action_id": "ci-stabilize-001",
            "priority": "P0",
            "owner": "Engineering",
            "timebox_hours": 4,
            "objective": "Restore green CI before new merges",
            "reason": "Build instability is a release blocker and amplifies downstream risk.",
            "success_criteria": "Main branch last build is successful and failure trend is reduced.",
            "execute_message": "Build instability detected. Trigger incident channel alert and triage top failing job.",
        })

    if facts.get("uat_state") in {"failure", "error", "inactive", "unknown", "not_found"}:
        actions.append({
            "action_id": "slack-alert-uat-failure-001",
            "priority": "P0",
            "owner": "Release Manager",
            "timebox_hours": 0.25,
            "objective": "Send Slack alert for UAT deployment risk",
            "reason": "UAT failure or unknown state impacts release confidence.",
            "success_criteria": "UAT status alert sent with remediation owner.",
            "execute_message": "UAT deployment status is risky. Validate deployment health and unblock release.",
            "alert_type": "uat_failure",
        })

    if facts.get("prod_state") in {"failure", "error", "inactive", "unknown", "not_found"}:
        actions.append({
            "action_id": "slack-alert-prod-failure-001",
            "priority": "P0",
            "owner": "Release Manager",
            "timebox_hours": 0.25,
            "objective": "Send Slack alert for PROD deployment risk",
            "reason": "Production risk needs immediate visibility and coordinated response.",
            "success_criteria": "PROD status alert sent with rollback-check reminder.",
            "execute_message": "PROD deployment status is risky. Start release incident protocol.",
            "alert_type": "prod_failure",
        })
        actions.append({
            "action_id": "prod-readiness-001",
            "priority": "P0",
            "owner": "Release Manager",
            "timebox_hours": 6,
            "objective": "Validate deployment readiness and rollback path",
            "reason": "Production deployment status indicates elevated release risk.",
            "success_criteria": "Smoke check gate defined and rollback checklist verified.",
            "execute_message": "Production readiness risk detected. Send rollback-check reminder to release channel.",
        })

    if facts.get("blocked_over_4d", 0) > 0:
        actions.append({
            "action_id": "blocker-escalation-001",
            "priority": "P1",
            "owner": "Product + Tech Lead",
            "timebox_hours": 2,
            "objective": "Escalate stories blocked for more than 4 days",
            "reason": "Long-running blockers directly reduce sprint throughput.",
            "success_criteria": "Each stale blocker has owner, escalation target, and ETA.",
            "execute_message": "Stale blockers found. Notify product and tech leads with escalation request.",
        })
    elif facts.get("blocked_total", 0) > 0:
        actions.append({
            "action_id": "blocker-triage-001",
            "priority": "P1",
            "owner": "Scrum Master",
            "timebox_hours": 1,
            "objective": "Run blocker triage and convert blockers into tracked tasks",
            "reason": "Active blockers need explicit ownership to prevent spillover.",
            "success_criteria": "All blockers mapped to owner and next action.",
            "execute_message": "Blockers present. Send triage reminder to scrum channel.",
        })

    if facts.get("pending_review_over_48h", 0) > 0:
        actions.append({
            "action_id": "review-sla-001",
            "priority": "P1",
            "owner": "Engineering Manager",
            "timebox_hours": 2,
            "objective": "Clear PR review backlog older than 48 hours",
            "reason": "PR latency is slowing delivery and integration speed.",
            "success_criteria": "PRs older than 48 hours are reduced to near zero.",
            "execute_message": "PR review SLA breach detected. Send reminder to reviewer group.",
        })
    elif facts.get("pending_reviews", 0) > 0:
        actions.append({
            "action_id": "review-window-001",
            "priority": "P2",
            "owner": "Engineering Team",
            "timebox_hours": 1,
            "objective": "Introduce fixed daily review windows",
            "reason": "Small review queues can become bottlenecks without cadence.",
            "success_criteria": "Daily review windows scheduled and followed.",
            "execute_message": "Pending reviews found. Send review-window reminder.",
        })

    if facts.get("sprint_completion_pct", 0) < 50:
        actions.append({
            "action_id": "scope-control-001",
            "priority": "P1",
            "owner": "Product Owner",
            "timebox_hours": 1,
            "objective": "Re-scope sprint to must-have stories",
            "reason": "Low completion indicates scope pressure for current sprint.",
            "success_criteria": "Backlog reprioritized and non-critical scope deferred.",
            "execute_message": "Low sprint completion detected. Send scope-control recommendation.",
        })

    if memory_feedback.get("skipped_due_to_webhook_last_5", 0) >= 2:
        actions.append({
            "action_id": "execution-routing-001",
            "priority": "P1",
            "owner": "Platform Owner",
            "timebox_hours": 0.5,
            "objective": "Configure AGENT_ALERT_WEBHOOK_URL for alert delivery",
            "reason": "Repeated skipped executions indicate missing webhook routing.",
            "success_criteria": "Webhook configured and at least one action alert executes successfully.",
            "execute_message": "Execution routing is not configured. Add AGENT_ALERT_WEBHOOK_URL.",
        })

    if not actions:
        actions.append({
            "action_id": "monitor-only-001",
            "priority": "P3",
            "owner": "Delivery Lead",
            "timebox_hours": 0.5,
            "objective": "Continue monitoring with no urgent intervention",
            "reason": "No major risk signals crossed thresholds this cycle.",
            "success_criteria": "Maintain delivery health and watch for trend changes.",
            "execute_message": "No urgent delivery risk. Monitoring update posted.",
        })

    for action in actions:
        if action.get("action_id") in memory_feedback.get("recurring_failed_action_ids", []):
            action["memory_note"] = "This action failed in multiple recent runs. Check credentials/connectivity before retry."

    return actions


def run_agentic_planner(final_report, max_cycles=2, memory_feedback=None):
    facts = _risk_facts(final_report)
    memory_feedback = memory_feedback or {}
    seen_action_ids = set()
    cycles = []

    for cycle in range(1, max_cycles + 1):
        proposed_actions = _decide_actions(facts, memory_feedback=memory_feedback)
        new_actions = [action for action in proposed_actions if action["action_id"] not in seen_action_ids]

        if not new_actions:
            cycles.append({
                "cycle": cycle,
                "observation": facts,
                "decisions": [],
                "status": "no_new_actions",
            })
            break

        for action in new_actions:
            seen_action_ids.add(action["action_id"])

        cycles.append({
            "cycle": cycle,
            "observation": facts,
            "decisions": new_actions,
            "status": "actions_generated",
        })

    action_queue = [
        action
        for cycle in cycles
        for action in cycle.get("decisions", [])
    ]

    action_queue = _sort_actions_by_priority(action_queue)

    return {
        "enabled": True,
        "mode": "observe-think-decide",
        "autonomy_level": "semi-autonomous",
        "goal": "Reduce delivery risk and improve sprint throughput",
        "observed_facts": facts,
        "memory_feedback": memory_feedback,
        "cycles_executed": len(cycles),
        "cycles": cycles,
        "action_queue": action_queue,
    }


def _post_webhook_alert(webhook_url, payload):
    request = urllib_request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib_request.urlopen(request, timeout=15) as response:
        return response.getcode()


def _create_jira_escalation_ticket(action, run_id):
    jira_server = os.getenv("JIRA_SERVER", "").strip().rstrip("/")
    jira_email = os.getenv("JIRA_EMAIL", "").strip()
    jira_token = os.getenv("JIRA_TOKEN", "").strip()
    jira_project = os.getenv("JIRA_ESCALATION_PROJECT", "SHOP").strip() or "SHOP"

    if not all([jira_server, jira_email, jira_token]):
        return {
            "ok": False,
            "status": "skipped",
            "detail": "Jira escalation skipped: missing JIRA_SERVER/JIRA_EMAIL/JIRA_TOKEN.",
        }

    original_action_id = action.get("original_action_id", "unknown-action")
    evidence = action.get("outcome_evidence", "No evidence")
    hours_since_action = action.get("hours_since_action", "unknown")

    payload = {
        "fields": {
            "project": {"key": jira_project},
            "summary": f"[AUTO-ESCALATION] {original_action_id} unresolved after 24h",
            "description": (
                f"Run: {run_id}\n"
                f"Original action: {original_action_id}\n"
                f"Age hours: {hours_since_action}\n"
                f"Evidence: {evidence}\n"
                f"Escalation objective: {action.get('objective', 'N/A')}"
            ),
            "issuetype": {"name": "Task"},
            "labels": ["delivery-health-agent", "auto-escalation"],
        }
    }

    url = f"{jira_server}/rest/api/2/issue"
    token = base64.b64encode(f"{jira_email}:{jira_token}".encode("utf-8")).decode("ascii")
    request = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
            issue_key = body.get("key", "unknown")
            return {
                "ok": True,
                "status": "executed",
                "detail": f"Jira escalation ticket created: {issue_key}",
            }
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": "failed",
            "detail": f"Jira escalation failed: {exc}",
        }


def _notify_senior_stakeholders(action, run_id, default_webhook_url):
    stakeholder_webhook = os.getenv("AGENT_STAKEHOLDER_WEBHOOK_URL", "").strip() or default_webhook_url

    if not stakeholder_webhook:
        return {
            "ok": False,
            "status": "skipped",
            "detail": "Stakeholder notification skipped: no stakeholder webhook configured.",
        }

    payload = {
        "text": (
            "[Delivery Health Agent][P0 Escalation] "
            f"{action.get('objective', 'Escalation triggered')} | "
            f"Original action: {action.get('original_action_id', 'unknown')} | "
            f"Evidence: {action.get('outcome_evidence', 'No evidence')}"
        ),
        "run_id": run_id,
        "action_id": action.get("action_id"),
        "original_action_id": action.get("original_action_id"),
        "priority": action.get("priority", "P0"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    try:
        status_code = _post_webhook_alert(stakeholder_webhook, payload)
        return {
            "ok": True,
            "status": "executed",
            "detail": f"Stakeholder notification sent (HTTP {status_code}).",
        }
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError) as exc:
        return {
            "ok": False,
            "status": "failed",
            "detail": f"Stakeholder notification failed: {exc}",
        }


def execute_actions(actions, run_id=None, execute_enabled=False):
    executed = []
    webhook_url = os.getenv("AGENT_ALERT_WEBHOOK_URL", "").strip()

    for action in actions:
        action_id = action.get("action_id")
        text = action.get("execute_message") or action.get("objective") or "No message"
        payload = {
            "text": f"[Delivery Health Agent] {text}",
            "action_id": action_id,
            "alert_type": action.get("alert_type", "general"),
            "priority": action.get("priority", "P3"),
            "owner": action.get("owner", "Unassigned"),
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

        if action.get("escalation_action"):
            if not execute_enabled:
                executed.append({
                    "action_id": action_id,
                    "status": "dry_run",
                    "detail": "Execution disabled. Escalation planned only.",
                })
                continue

            jira_result = _create_jira_escalation_ticket(action, run_id)
            notify_result = _notify_senior_stakeholders(action, run_id, webhook_url)

            parts = [jira_result.get("detail"), notify_result.get("detail")]
            success_count = sum(1 for item in [jira_result, notify_result] if item.get("ok"))
            skipped_count = sum(1 for item in [jira_result, notify_result] if item.get("status") == "skipped")

            if success_count > 0:
                status = "executed"
            elif skipped_count == 2:
                status = "skipped"
            else:
                status = "failed"

            executed.append({
                "action_id": action_id,
                "status": status,
                "detail": " | ".join(part for part in parts if part),
            })
            continue

        if not execute_enabled:
            executed.append({
                "action_id": action_id,
                "status": "dry_run",
                "detail": "Execution disabled. Planned action only.",
            })
            continue

        if not webhook_url:
            executed.append({
                "action_id": action_id,
                "status": "skipped",
                "detail": "AGENT_ALERT_WEBHOOK_URL not configured.",
            })
            continue

        try:
            status_code = _post_webhook_alert(webhook_url, payload)
            executed.append({
                "action_id": action_id,
                "status": "executed",
                "detail": f"Alert sent (HTTP {status_code}).",
            })
        except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError) as exc:
            executed.append({
                "action_id": action_id,
                "status": "failed",
                "detail": str(exc),
            })

    return executed


