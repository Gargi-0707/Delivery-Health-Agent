# -*- coding: utf-8 -*-
"""
integrations/jira_client.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Jira API client: authenticates, fetches all project issues, and
processes them into sprint-segregated summary structures.
"""

import re
from collections import Counter
from datetime import datetime, timezone
from itertools import islice

from jira import JIRA

from core.config import (
    JIRA_EMAIL,
    JIRA_SERVER,
    JIRA_STATUS_ALIASES,
    JIRA_STATUS_ORDER,
    JIRA_TOKEN,
    STORY_POINTS_FIELD,
)
from core.logging import METRICS, log_event
from core.utils import normalize_jira_status, parse_dt


# ---------------------------------------------------------------------------
# Connection & Fetch
# ---------------------------------------------------------------------------

def connect_jira() -> JIRA:
    """Return an authenticated JIRA client or raise RuntimeError."""
    try:
        return JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_TOKEN), timeout=60)
    except Exception as exc:
        METRICS.record_external_api_failure("jira")
        log_event("error", "jira_auth_failed", error_type=type(exc).__name__, error_message=str(exc))
        raise RuntimeError("❌ Jira authentication failed") from exc


def fetch_jira_issues(jira_client: JIRA) -> list:
    """Fetch all issues for the SHOP project ordered by created date."""
    jql = "project = SHOP ORDER BY created DESC"
    return jira_client.search_issues(
        jql,
        maxResults=100,
        fields=(
            f"summary,description,status,issuetype,assignee,"
            f"{STORY_POINTS_FIELD},customfield_10020,labels,created,updated"
        ),
    )


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_sprint(issues: list, pulls: list) -> tuple:
    """
    Process Jira issues and GitHub PRs into summary structures.

    Returns:
        (completion_pct, overall_completion_pct, sprint_summary,
         jira_summary, github_summary)
    """
    total_sp = done_sp = completed_tasks = blocked_tasks = 0
    sprint_total_sp = sprint_done_sp = sprint_total_tasks = sprint_completed_tasks = 0
    total_review_comments = pending_reviews = 0

    status_counts: Counter = Counter()
    canonical_status_counts: Counter = Counter()
    type_counts: Counter = Counter()
    created_last_7d = updated_last_7d = 0

    blocked_details: list = []
    sprint_summary: list = []
    pr_map: dict = {}
    sprint_segregation: dict = {}
    sprint_metrics: dict = {}

    now_utc = datetime.now(timezone.utc)

    # ── Map PRs ──────────────────────────────────────────────────────────────
    pr_details: list = []
    for pr in pulls:
        match = re.search(r"[A-Z]+-\d+", pr.title)
        if match:
            pr_map[match.group()] = pr.state

        if pr.state == "open":
            pending_reviews += 1

        total_review_comments += getattr(pr, "review_comments", 0) + getattr(pr, "comments", 0)

        pr_comments_text = []
        try:
            for comment in list(pr.get_review_comments())[:10]:
                pr_comments_text.append(f"Review: {comment.body}")
            for comment in list(pr.get_issue_comments())[:10]:
                pr_comments_text.append(f"General: {comment.body}")
        except Exception:
            pass

        pr_shop_id = match.group() if match else None
        is_merged = getattr(pr, "merged", False)
        if is_merged:
            pr_status = "merged (review done)"
        elif pr.state == "closed":
            pr_status = "closed (unmerged)"
        else:
            pr_status = "pending review"

        pr_details.append({
            "shop_id": pr_shop_id,
            "branch": getattr(getattr(pr, "head", None), "ref", "unknown"),
            "status": pr_status,
            "merged": is_merged,
            "iterations": getattr(pr, "review_comments", 0) + getattr(pr, "comments", 0),
            "comments_text": pr_comments_text,
            "description": getattr(pr, "body", "") or "",
        })

    # ── Process Issues ────────────────────────────────────────────────────────
    for issue in issues:
        fields = issue.fields

        # Story points
        sp = getattr(fields, STORY_POINTS_FIELD, None)
        if sp is None:
            for attr in dir(fields):
                if "customfield" in attr:
                    val = getattr(fields, attr)
                    if isinstance(val, (int, float)):
                        sp = val
                        break
        sp = sp or 0

        # Sprint extraction
        sprint_info = getattr(fields, "customfield_10020", None)
        sprint_names = []
        if sprint_info:
            if isinstance(sprint_info, list):
                for s in sprint_info:
                    if isinstance(s, dict):
                        sprint_names.append(s.get("name", "Unknown Sprint"))
                    elif hasattr(s, "name"):
                        sprint_names.append(s.name)
                    else:
                        m = re.search(r"name=([^,\]]+)", str(s))
                        sprint_names.append(m.group(1) if m else "Unknown Sprint")
            else:
                sprint_names.append(str(sprint_info))

        sprint_names = [sprint_names[-1]] if sprint_names else ["Backlog"]

        status = fields.status.name
        canonical_status = normalize_jira_status(status)
        status_category = getattr(getattr(fields, "status", None), "statusCategory", None)
        status_category_name = (getattr(status_category, "name", "") or "").lower()
        issue_type = getattr(getattr(fields, "issuetype", None), "name", "Unknown")
        labels = getattr(fields, "labels", [])
        updated_at = parse_dt(getattr(fields, "updated", None))
        created_at = parse_dt(getattr(fields, "created", None))
        assignee_obj = getattr(fields, "assignee", None)
        assignee_name = getattr(assignee_obj, "displayName", "Unassigned") if assignee_obj else "Unassigned"

        status_counts[status] += 1
        canonical_status_counts[canonical_status] += 1
        type_counts[issue_type] += 1

        if created_at and (now_utc - created_at).days <= 7:
            created_last_7d += 1
        if updated_at and (now_utc - updated_at).days <= 7:
            updated_last_7d += 1

        total_sp += sp
        is_done = status_category_name == "done" or canonical_status == "Completed"

        if sprint_names != ["Backlog"]:
            sprint_total_sp += sp
            sprint_total_tasks += 1
            if is_done:
                sprint_done_sp += sp
                sprint_completed_tasks += 1
                done_sp += sp
                completed_tasks += 1

        is_blocked = status.lower() == "blocked" or "blocked" in [l.lower() for l in labels]
        if is_blocked:
            blocked_tasks += 1
            blocked_for_hours = over_4d = remaining_to_4d = None
            if updated_at:
                blocked_for_hours = round((now_utc - updated_at).total_seconds() / 3600, 1)
                over_4d = round(max(0.0, blocked_for_hours - 96.0), 1)
                remaining_to_4d = round(max(0.0, 96.0 - blocked_for_hours), 1)
            blocked_details.append({
                "id": issue.key,
                "status": status,
                "assignee": assignee_name,
                "blocked_for_hours": blocked_for_hours,
                "over_4d_by_hours": over_4d,
                "remaining_to_4d_hours": remaining_to_4d,
            })

        issue_item = {
            "id": issue.key,
            "title": getattr(fields, "summary", ""),
            "description": getattr(fields, "description", "") or "",
            "status": status,
            "assignee": assignee_name,
            "points": sp,
            "blocked": is_blocked,
            "has_pr": issue.key in pr_map,
            "pr_state": pr_map.get(issue.key, "none"),
        }

        for s_name in sprint_names:
            if s_name not in sprint_segregation:
                sprint_segregation[s_name] = []
                sprint_metrics[s_name] = {"total": 0, "completed": 0, "blocked": 0, "points_total": 0, "points_done": 0}
            sprint_segregation[s_name].append(issue_item)
            sprint_metrics[s_name]["total"] += 1
            sprint_metrics[s_name]["points_total"] += sp
            if is_done:
                sprint_metrics[s_name]["completed"] += 1
                sprint_metrics[s_name]["points_done"] += sp
            if is_blocked:
                sprint_metrics[s_name]["blocked"] += 1

        sprint_summary.append(issue_item)

    # ── Completion calculations ───────────────────────────────────────────────
    task_progress = (sprint_completed_tasks / sprint_total_tasks * 100) if sprint_total_tasks > 0 else 0
    point_progress = (sprint_done_sp / sprint_total_sp * 100) if sprint_total_sp > 0 else task_progress
    overall_point_progress = (
        (sprint_done_sp / total_sp * 100)
        if total_sp > 0
        else (sprint_completed_tasks / len(issues) * 100)
    )

    completion = round(point_progress, 2)
    overall_completion = round(overall_point_progress, 2)
    total_tasks = len(issues)

    jira_summary = {
        "sprint_progress_pct": completion,
        "overall_project_progress_pct": overall_completion,
        "overall_task_completion_pct": round((sprint_completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 2),
        "sprint_task_completion_pct": round(task_progress, 2),
        "total_tasks": total_tasks,
        "completed": completed_tasks,
        "blocked": blocked_tasks,
        "blocked_details": blocked_details,
        "issue_count": total_tasks,
        "status_counts": dict(status_counts),
        "canonical_status_counts": {label: canonical_status_counts.get(label, 0) for label in JIRA_STATUS_ORDER},
        "type_counts": dict(type_counts),
        "activity": {
            "created_last_7d": created_last_7d,
            "updated_last_7d": updated_last_7d,
            "open_items": max(0, total_tasks - completed_tasks - blocked_tasks),
        },
        "issues": sprint_segregation,
        "sprint_metrics": sprint_metrics,
    }

    github_summary = {
        "total_prs": len(pulls),
        "pending_reviews": pending_reviews,
        "review_comments": total_review_comments,
        "pending_review_over_48h": sum(
            1
            for pr in pulls
            if pr.state == "open"
            and parse_dt(getattr(pr, "created_at", None))
            and (now_utc - parse_dt(getattr(pr, "created_at", None))).total_seconds() >= 48 * 3600
        ),
        "test_coverage_pct": None,
        "prs": pr_details,
    }

    return completion, overall_completion, sprint_summary, jira_summary, github_summary
