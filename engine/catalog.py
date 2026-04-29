# -*- coding: utf-8 -*-
"""
engine/catalog.py
~~~~~~~~~~~~~~~~~
ACTION_CATALOG — the single source of truth for every valid action
the agent is authorised to take. The LLM MUST only choose action_ids
that exist in this dict; all others are rejected as hallucinations.
"""


ACTION_CATALOG: dict = {
    "slack-alert-build-failure-001": {
        "description": "Send Slack alert for a CI/CD build failure.",
        "trigger_condition": "build_failures > 0 OR last_build in [failure, failed, cancelled]",
        "priority": "P0", "owner": "Engineering", "timebox_hours": 0.25,
        "objective": "Send Slack alert for build failure",
        "reason": "Build failure requires immediate team awareness.",
        "success_criteria": "Build failure alert sent with triage context.",
        "execute_message": "Build failure detected. Immediate triage required.",
        "alert_type": "build_failure",
    },
    "ci-stabilize-001": {
        "description": "Restore green CI before allowing new merges.",
        "trigger_condition": "build_failures > 0 OR last_build in [failure, failed, cancelled]",
        "priority": "P0", "owner": "Engineering", "timebox_hours": 4,
        "objective": "Restore green CI before new merges",
        "reason": "Build instability is a release blocker.",
        "success_criteria": "Main branch last build is successful.",
        "execute_message": "Build instability detected. Trigger incident channel alert and triage top failing job.",
    },
    "slack-alert-uat-failure-001": {
        "description": "Send Slack alert for UAT deployment risk.",
        "trigger_condition": "uat_state in [failure, error, inactive, unknown, not_found]",
        "priority": "P0", "owner": "Release Manager", "timebox_hours": 0.25,
        "objective": "Send Slack alert for UAT deployment risk",
        "reason": "UAT failure or unknown state impacts release confidence.",
        "success_criteria": "UAT status alert sent with remediation owner.",
        "execute_message": "UAT deployment status is risky. Validate deployment health and unblock release.",
        "alert_type": "uat_failure",
    },
    "slack-alert-prod-failure-001": {
        "description": "Send Slack alert for PROD deployment risk.",
        "trigger_condition": "prod_state in [failure, error, inactive, unknown, not_found]",
        "priority": "P0", "owner": "Release Manager", "timebox_hours": 0.25,
        "objective": "Send Slack alert for PROD deployment risk",
        "reason": "Production risk needs immediate visibility.",
        "success_criteria": "PROD status alert sent with rollback-check reminder.",
        "execute_message": "PROD deployment status is risky. Start release incident protocol.",
        "alert_type": "prod_failure",
    },
    "prod-readiness-001": {
        "description": "Validate deployment readiness and rollback path.",
        "trigger_condition": "prod_state in [failure, error, inactive, unknown, not_found]",
        "priority": "P0", "owner": "Release Manager", "timebox_hours": 6,
        "objective": "Validate deployment readiness and rollback path",
        "reason": "Production deployment status indicates elevated release risk.",
        "success_criteria": "Smoke check gate defined and rollback checklist verified.",
        "execute_message": "Production readiness risk detected. Send rollback-check reminder to release channel.",
    },
    "blocker-escalation-001": {
        "description": "Escalate stories blocked for more than 4 days.",
        "trigger_condition": "blocked_over_4d > 0",
        "priority": "P1", "owner": "Product + Tech Lead", "timebox_hours": 2,
        "objective": "Escalate stories blocked for more than 4 days",
        "reason": "Long-running blockers directly reduce sprint throughput.",
        "success_criteria": "Each stale blocker has owner, escalation target, and ETA.",
        "execute_message": "Stale blockers found. Notify product and tech leads with escalation request.",
    },
    "blocker-triage-001": {
        "description": "Run blocker triage and convert blockers into tracked tasks.",
        "trigger_condition": "blocked_total > 0 AND blocked_over_4d == 0",
        "priority": "P1", "owner": "Scrum Master", "timebox_hours": 1,
        "objective": "Run blocker triage and convert blockers into tracked tasks",
        "reason": "Active blockers need explicit ownership to prevent spillover.",
        "success_criteria": "All blockers mapped to owner and next action.",
        "execute_message": "Blockers present. Send triage reminder to scrum channel.",
    },
    "review-sla-001": {
        "description": "Clear PR review backlog older than 48 hours.",
        "trigger_condition": "pending_review_over_48h > 0",
        "priority": "P1", "owner": "Engineering Manager", "timebox_hours": 2,
        "objective": "Clear PR review backlog older than 48 hours",
        "reason": "PR latency is slowing delivery and integration speed.",
        "success_criteria": "PRs older than 48 hours are reduced to near zero.",
        "execute_message": "PR review SLA breach detected. Send reminder to reviewer group.",
    },
    "review-window-001": {
        "description": "Introduce fixed daily review windows.",
        "trigger_condition": "pending_reviews > 0 AND pending_review_over_48h == 0",
        "priority": "P2", "owner": "Engineering Team", "timebox_hours": 1,
        "objective": "Introduce fixed daily review windows",
        "reason": "Small review queues can become bottlenecks without cadence.",
        "success_criteria": "Daily review windows scheduled and followed.",
        "execute_message": "Pending reviews found. Send review-window reminder.",
    },
    "scope-control-001": {
        "description": "Re-scope sprint to must-have stories when completion is critically low.",
        "trigger_condition": "sprint_completion_pct < 50",
        "priority": "P1", "owner": "Product Owner", "timebox_hours": 1,
        "objective": "Re-scope sprint to must-have stories",
        "reason": "Low completion indicates scope pressure for current sprint.",
        "success_criteria": "Backlog reprioritized and non-critical scope deferred.",
        "execute_message": "Low sprint completion detected. Send scope-control recommendation.",
    },
    "execution-routing-001": {
        "description": "Configure AGENT_ALERT_WEBHOOK_URL when repeated skips detected.",
        "trigger_condition": "skipped_due_to_webhook_last_5 >= 2",
        "priority": "P1", "owner": "Platform Owner", "timebox_hours": 0.5,
        "objective": "Configure AGENT_ALERT_WEBHOOK_URL for alert delivery",
        "reason": "Repeated skipped executions indicate missing webhook routing.",
        "success_criteria": "Webhook configured and at least one action alert executes successfully.",
        "execute_message": "Execution routing is not configured. Add AGENT_ALERT_WEBHOOK_URL.",
    },
    "monitor-only-001": {
        "description": "No urgent intervention needed – continue monitoring.",
        "trigger_condition": "No risk thresholds breached",
        "priority": "P3", "owner": "Delivery Lead", "timebox_hours": 0.5,
        "objective": "Continue monitoring with no urgent intervention",
        "reason": "No major risk signals crossed thresholds this cycle.",
        "success_criteria": "Maintain delivery health and watch for trend changes.",
        "execute_message": "No urgent delivery risk. Monitoring update posted.",
    },
    "strategic-coaching-report": {
        "action_id": "strategic-coaching-report",
        "priority": "P2",
        "owner": "Stakeholders",
        "description": "Send a high-level AI Strategic Coaching Report to Slack.",
        "trigger_condition": "Always run this at least once every 24h, or if the Health Score is low.",
        "alert_type": "strategic",
        "execute_message": "AI Strategic Coaching Report ready for delivery.",
    },
}


def _build_action_from_catalog(action_id: str, extra: dict | None = None) -> dict:
    """Return a fully-populated action dict from the catalog."""
    template = ACTION_CATALOG.get(action_id, {})
    action = {
        "action_id": action_id,
        **{k: v for k, v in template.items() if k not in {"description", "trigger_condition"}},
    }
    if extra:
        action.update(extra)
    return action


def _sort_actions_by_priority(actions: list) -> list:
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(actions, key=lambda item: priority_rank.get(item.get("priority", "P3"), 3))
