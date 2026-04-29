# -*- coding: utf-8 -*-
"""
intelligence/team.py
~~~~~~~~~~~~~~~~~~~~
Team-level analytics:
  - Team capacity (per-assignee SP load)
  - Sprint retrospectives (per-sprint health)
  - Active work snapshot (standup view)
"""

import re
from typing import Any, Dict, List


DONE_STATUSES = {"done", "completed", "closed", "resolved"}
NOT_STARTED_STATUSES = {"to do", "open", "backlog"}


def _compute_team_capacity(report: Dict[str, Any]) -> Dict[str, Any]:
    """Per-assignee story point load with overcommitment flag."""
    jira = report.get("jira", {})
    issues_by_sprint = jira.get("issues", {})

    assignee_data: Dict[str, Dict] = {}

    for sprint_name, issues in issues_by_sprint.items():
        for issue in issues:
            assignee = issue.get("assignee") or "Unassigned"
            pts = float(issue.get("points") or 0)
            status = str(issue.get("status") or "").lower()
            issue_type = str(issue.get("type") or "Task")

            if assignee not in assignee_data:
                assignee_data[assignee] = {
                    "assignee": assignee,
                    "total_issues": 0,
                    "total_sp": 0.0,
                    "completed_sp": 0.0,
                    "in_progress_sp": 0.0,
                    "blocked_issues": 0,
                    "sprints_active": set(),
                    "issue_types": {},
                }
            d = assignee_data[assignee]
            d["total_issues"] += 1
            d["total_sp"] += pts
            d["sprints_active"].add(sprint_name)
            d["issue_types"][issue_type] = d["issue_types"].get(issue_type, 0) + 1

            if status in DONE_STATUSES:
                d["completed_sp"] += pts
            elif issue.get("blocked"):
                d["blocked_issues"] += 1
            else:
                d["in_progress_sp"] += pts

    OVERCOMMIT_THRESHOLD = 40.0
    result = []
    for assignee, d in sorted(assignee_data.items(), key=lambda x: -x[1]["total_sp"]):
        completion_rate = round((d["completed_sp"] / d["total_sp"] * 100) if d["total_sp"] > 0 else 0, 1)
        load_flag = (
            "Overcommitted" if d["total_sp"] > OVERCOMMIT_THRESHOLD
            else "Balanced" if d["total_sp"] > 10
            else "Under-utilised"
        )
        result.append({
            "assignee": assignee,
            "total_issues": d["total_issues"],
            "total_sp": round(d["total_sp"], 1),
            "completed_sp": round(d["completed_sp"], 1),
            "in_progress_sp": round(d["in_progress_sp"], 1),
            "blocked_issues": d["blocked_issues"],
            "completion_rate_pct": completion_rate,
            "sprints_active": len(d["sprints_active"]),
            "issue_types": d["issue_types"],
            "load_status": load_flag,
        })

    total_sp = sum(r["total_sp"] for r in result)
    return {
        "team_members": len(result),
        "total_sp_across_team": round(total_sp, 1),
        "members": result,
    }


def _compute_sprint_retrospectives(report: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-generate a per-sprint retrospective: completion rate, blocked rate, SP velocity."""
    jira = report.get("jira", {})
    sprint_metrics = jira.get("sprint_metrics", {})
    issues_by_sprint = jira.get("issues", {})

    retros = []
    for s_name, m in sprint_metrics.items():
        if s_name == "Backlog":
            continue
        total = m.get("total", 0) or 0
        completed = m.get("completed", 0) or 0
        blocked = m.get("blocked", 0) or 0
        pts_total = float(m.get("points_total", 0) or 0)
        pts_done = float(m.get("points_done", 0) or 0)

        completion_rate = round((completed / total * 100) if total > 0 else 0, 1)
        sp_completion_rate = round((pts_done / pts_total * 100) if pts_total > 0 else 0, 1)
        blocked_rate = round((blocked / total * 100) if total > 0 else 0, 1)

        sprint_issues = issues_by_sprint.get(s_name, [])
        completed_issues = [i["title"] for i in sprint_issues if str(i.get("status", "")).lower() in DONE_STATUSES][:3]
        open_issues = [i["title"] for i in sprint_issues if str(i.get("status", "")).lower() not in DONE_STATUSES][:3]
        blocked_items = [i["title"] for i in sprint_issues if i.get("blocked")][:3]

        nums = re.findall(r'\d+', s_name)
        sprint_order = int(nums[0]) if nums else 0

        retros.append({
            "sprint": s_name,
            "sprint_order": sprint_order,
            "total_issues": total,
            "completed_issues": completed,
            "blocked_issues": blocked,
            "points_assigned": pts_total,
            "points_completed": pts_done,
            "issue_completion_rate_pct": completion_rate,
            "sp_completion_rate_pct": sp_completion_rate,
            "blocked_rate_pct": blocked_rate,
            "health": (
                "Healthy" if sp_completion_rate >= 70
                else "At Risk" if sp_completion_rate >= 30
                else "Critical"
            ),
            "highlights": {
                "completed": completed_issues,
                "still_open": open_issues,
                "blocked": blocked_items,
            },
        })

    retros.sort(key=lambda x: x["sprint_order"])
    avg_sp_rate = round(sum(r["sp_completion_rate_pct"] for r in retros) / len(retros), 1) if retros else 0

    return {
        "sprints": retros,
        "avg_sp_completion_rate_pct": avg_sp_rate,
        "summary": (
            "Team is delivering consistently." if avg_sp_rate >= 70
            else "Team is at risk — SP completion below 70% on average." if avg_sp_rate >= 30
            else "Critical under-delivery — average SP completion below 30%."
        ),
    }


def _compute_active_work_snapshot(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Current active-work standup view: groups in-progress issues by assignee.
    'Active' = status is neither Done/Closed nor To Do/Open.
    """
    jira = report.get("jira", {})
    issues_by_sprint = jira.get("issues", {})

    assignee_work: Dict[str, List[Dict]] = {}
    total_active_issues = 0
    total_active_sp = 0.0

    for sprint_name, issues in issues_by_sprint.items():
        if sprint_name == "Backlog":
            continue
        for issue in issues:
            status_lower = str(issue.get("status") or "").lower().strip()
            if status_lower in DONE_STATUSES or status_lower in NOT_STARTED_STATUSES:
                continue

            assignee = issue.get("assignee") or "Unassigned"
            pts = float(issue.get("points") or 0)

            assignee_work.setdefault(assignee, []).append({
                "id": issue.get("id"),
                "title": issue.get("title", ""),
                "status": issue.get("status"),
                "sprint": sprint_name,
                "points": pts,
                "blocked": bool(issue.get("blocked")),
                "has_pr": bool(issue.get("has_pr")),
                "type": issue.get("type") or "Task",
            })
            total_active_issues += 1
            total_active_sp += pts

    members = []
    for assignee, issues_list in sorted(assignee_work.items()):
        blocked_items = [i for i in issues_list if i["blocked"]]
        members.append({
            "assignee": assignee,
            "active_issues": len(issues_list),
            "active_sp": round(sum(i["points"] for i in issues_list), 1),
            "blocked_count": len(blocked_items),
            "issues": sorted(issues_list, key=lambda x: (-x["points"], x["blocked"])),
        })

    members.sort(key=lambda x: -x["active_sp"])

    return {
        "total_active_issues": total_active_issues,
        "total_active_sp": round(total_active_sp, 1),
        "assignee_count": len(members),
        "members": members,
        "snapshot_note": (
            f"{total_active_issues} issue(s) currently in progress "
            f"({total_active_sp:.0f} SP) across {len(members)} assignee(s). "
            "Active = status is neither Done/Closed nor To Do/Open."
        ),
    }
