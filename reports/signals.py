# -*- coding: utf-8 -*-
"""
reports/signals.py
~~~~~~~~~~~~~~~~~~
Deterministic health-signal and recommendation generators.
Reads pre-processed Jira, GitHub, and CI/CD summaries and
returns structured signal/recommendation lists — no LLM required.
"""


def build_health_signals(jira_summary: dict, github_summary: dict, cicd_summary: dict) -> list:
    """Return a list of human-readable health signal strings."""
    signals = []

    if cicd_summary.get("build_failures", 0) > 0:
        signals.append(f"{cicd_summary['build_failures']} build failures detected")

    if cicd_summary.get("last_build") in {"failure", "failed", "cancelled"}:
        signals.append(f"Last build status: {cicd_summary['last_build']}")

    prod_state = cicd_summary.get("environments", {}).get("prod", "unknown")
    if prod_state in {"failure", "error", "inactive", "unknown", "not_found"}:
        signals.append(f"Deployment risk in production (prod status: {prod_state})")

    blocked_details = jira_summary.get("blocked_details", [])
    blocked_over_4d = sum(1 for item in blocked_details if item.get("over_4d_by_hours", 0) > 0)
    if blocked_over_4d > 0:
        signals.append(f"{blocked_over_4d} stories blocked > 4 days")
    elif jira_summary.get("blocked", 0) > 0:
        signals.append(f"{jira_summary['blocked']} stories blocked")

    pending_review_over_48h = github_summary.get("pending_review_over_48h", 0)
    if pending_review_over_48h > 0:
        signals.append(f"{pending_review_over_48h} PRs pending review > 48 hrs")
    elif github_summary.get("pending_reviews", 0) > 0:
        signals.append(f"{github_summary['pending_reviews']} PRs pending review")

    coverage = github_summary.get("test_coverage_pct")
    if coverage is not None:
        signals.append(f"Test coverage at {coverage}%")

    if not signals:
        signals.append("No major delivery risks detected from current snapshots")

    return signals


def build_recommendations(jira_summary: dict, github_summary: dict, cicd_summary: dict) -> list:
    """Return up to 3 deterministic, actionable recommendation strings."""
    recommendations = []

    blocked_details = jira_summary.get("blocked_details", [])
    blocked_over_4d = [item for item in blocked_details if (item.get("over_4d_by_hours") or 0) > 0]
    if blocked_over_4d:
        issue_ids = ", ".join(item.get("id", "") for item in blocked_over_4d[:3] if item.get("id"))
        recommendations.append(
            f"Escalate blocked stories older than 4 days ({issue_ids}) and assign owners for same-day unblock."
        )
    elif jira_summary.get("blocked", 0) > 0:
        recommendations.append(
            "Run a blocker triage with dev + QA today and convert each blocker into a tracked action item."
        )

    if github_summary.get("pending_review_over_48h", 0) > 0:
        recommendations.append(
            "Create a review SLA lane for PRs older than 48 hours and clear the queue before new feature pickup."
        )
    elif github_summary.get("pending_reviews", 0) > 0:
        recommendations.append(
            "Timebox two review windows per day to reduce pending PRs and avoid merge bottlenecks."
        )

    if cicd_summary.get("build_failures", 0) > 0 or cicd_summary.get("last_build") in {"failure", "failed", "cancelled"}:
        recommendations.append(
            "Stabilize CI first: fix the top failing test/build step and enforce green build before merge."
        )

    prod_state = cicd_summary.get("environments", {}).get("prod", "unknown")
    if prod_state in {"failure", "error", "inactive", "unknown", "not_found"}:
        recommendations.append(
            "Add a production deployment smoke-check gate and verify rollback readiness before next release."
        )

    if jira_summary.get("sprint_progress_pct", 0) < 50:
        recommendations.append(
            "Re-scope sprint backlog to must-have items only and freeze low-priority work for this sprint."
        )

    while len(recommendations) < 3:
        recommendations.append(
            "Track daily delivery health in standup using blockers, pending reviews, and build status trends."
        )

    return recommendations[:3]


def build_aggregated_report(jira_summary: dict, github_summary: dict, cicd_summary: dict) -> dict:
    """Combine the three source summaries with derived signals."""
    return {
        "jira": jira_summary,
        "github": github_summary,
        "cicd": cicd_summary,
        "signals": build_health_signals(jira_summary, github_summary, cicd_summary),
    }
