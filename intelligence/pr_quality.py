# -*- coding: utf-8 -*-
"""
intelligence/pr_quality.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
PR quality analysis (rework rate, review iterations) and stale issue
detection (carry-over risk from past sprints).
"""

from typing import Any, Dict, List


DONE_STATUSES = {"done", "completed", "closed", "resolved"}


def _compute_pr_quality_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    PR quality: rework rate, avg review iterations, high-churn PRs (3+),
    and a composite quality score.
    """
    github = report.get("github", {})
    prs = github.get("prs", [])

    if not prs:
        return {"available": False, "reason": "No PR data found."}

    total_prs = len(prs)
    merged_prs = [p for p in prs if p.get("merged")]
    pending_prs = [p for p in prs if not p.get("merged")]
    iterations = [int(p.get("iterations", 0)) for p in prs]
    avg_iterations = round(sum(iterations) / len(iterations), 2) if iterations else 0
    high_rework = [p for p in prs if int(p.get("iterations", 0)) >= 3]
    rework_rate = round(len(high_rework) / total_prs * 100, 1) if total_prs > 0 else 0

    pending_over_48h = github.get("pending_review_over_48h", 0) or 0
    total_comments = github.get("review_comments", 0) or 0
    quality_score = max(0, 100 - (rework_rate * 0.5) - (pending_over_48h * 10))

    return {
        "total_prs": total_prs,
        "merged_prs": len(merged_prs),
        "pending_prs": len(pending_prs),
        "pending_over_48h": pending_over_48h,
        "avg_review_iterations": avg_iterations,
        "rework_rate_pct": rework_rate,
        "total_review_comments": total_comments,
        "high_rework_prs": [
            {
                "id": p.get("shop_id") or p.get("id"),
                "branch": p.get("branch", ""),
                "iterations": p.get("iterations"),
                "merged": p.get("merged", False),
            }
            for p in high_rework
        ],
        "quality_score": round(quality_score, 1),
        "quality_label": (
            "High" if quality_score >= 80
            else "Medium" if quality_score >= 50
            else "Low"
        ),
        "insight": (
            f"Rework rate is {rework_rate}% ({len(high_rework)} PRs with 3+ review iterations). "
            f"Avg iterations per PR: {avg_iterations}. "
            f"{pending_over_48h} PR(s) pending review over 48h."
        ),
    }


def _detect_stale_issues(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detect issues stuck in non-done statuses inside past (non-Backlog) sprints.
    These are carry-over risks — items that were planned but never closed.
    """
    jira = report.get("jira", {})
    issues_by_sprint = jira.get("issues", {})

    stale = []
    for sprint_name, issues in issues_by_sprint.items():
        if sprint_name == "Backlog":
            continue
        for issue in issues:
            status = str(issue.get("status") or "").lower()
            if status not in DONE_STATUSES:
                pts = float(issue.get("points") or 0)
                stale.append({
                    "id": issue.get("id"),
                    "title": issue.get("title", ""),
                    "sprint": sprint_name,
                    "status": issue.get("status"),
                    "assignee": issue.get("assignee") or "Unassigned",
                    "points": pts,
                    "blocked": issue.get("blocked", False),
                    "has_pr": issue.get("has_pr", False),
                    "risk_level": (
                        "Critical" if issue.get("blocked")
                        else "High" if status in {"to do", "open"}
                        else "Medium"
                    ),
                })

    stale.sort(key=lambda x: (
        0 if x["risk_level"] == "Critical" else 1 if x["risk_level"] == "High" else 2,
        -x["points"]
    ))

    total_stale_sp = sum(i["points"] for i in stale)
    critical = [i for i in stale if i["risk_level"] == "Critical"]
    high = [i for i in stale if i["risk_level"] == "High"]

    return {
        "total_stale_issues": len(stale),
        "total_stale_sp": round(total_stale_sp, 1),
        "critical_count": len(critical),
        "high_risk_count": len(high),
        "issues": stale[:20],
        "insight": (
            f"{len(stale)} issue(s) are stuck in past sprints (not Done). "
            f"Total carry-over risk: {total_stale_sp:.0f} SP. "
            f"{len(critical)} blocked, {len(high)} not started."
        ),
    }
