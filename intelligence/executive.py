# -*- coding: utf-8 -*-
"""
intelligence/executive.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Executive-level reporting and planning:
  - _generate_executive_summary()     — deterministic boardroom-ready text
  - _compute_sprint_over_sprint_trend() — trend analysis across sprints
  - _build_scenarios()                — Optimistic / Realistic / Pessimistic
  - _build_recommendations()          — actionable recommendation list
  - _evaluate_risks()                 — risk signal extraction
  - _build_sprint_plan()              — next sprint capacity guidance
  - _predict_next_sprint()            — predicted next sprint issues
"""

import json
import os
import re
from typing import Any, Dict, List
from urllib import request as urllib_request

try:
    from groq import Groq as _GroqClient
    _GROQ_SDK_AVAILABLE = True
except ImportError:
    _GroqClient = None
    _GROQ_SDK_AVAILABLE = False

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk evaluation
# ---------------------------------------------------------------------------

def _evaluate_risks(report: Dict[str, Any], facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    risks = []

    if facts.get("blocked_over_4d", 0) > 0:
        risks.append({"risk_type": "blocked_over_4d", "severity": "high", "description": f"{facts['blocked_over_4d']} stories blocked for over 4 days.", "reduction_pct": 20})
    if facts.get("build_failures", 0) > 0:
        risks.append({"risk_type": "build_failures", "severity": "high", "description": f"{facts['build_failures']} build failures detected.", "reduction_pct": 15})
    if facts.get("pending_review_over_48h", 0) > 0:
        risks.append({"risk_type": "pending_review_over_48h", "severity": "medium", "description": f"{facts['pending_review_over_48h']} PRs pending review for over 48 hours.", "reduction_pct": 10})

    uat_state = facts.get("uat_state", "unknown")
    if str(uat_state).lower() in {"failure", "error", "inactive", "unknown", "not_found"}:
        risks.append({"risk_type": "uat_state_risky", "severity": "medium", "description": f"UAT deployment state is risky ({uat_state}).", "reduction_pct": 10})

    prod_state = facts.get("prod_state", "unknown")
    if str(prod_state).lower() in {"failure", "error", "inactive", "unknown", "not_found"}:
        risks.append({"risk_type": "prod_state_risky", "severity": "high", "description": f"Production deployment state is risky ({prod_state}).", "reduction_pct": 15})

    slack_summary = report.get("slack", {})
    category_counts = slack_summary.get("category_counts", {})
    if category_counts.get("issues", 0) > 0 or category_counts.get("delivery_risks", 0) > 0:
        risks.append({"risk_type": "slack_escalation", "severity": "low", "description": "Slack escalation or delivery risk signals detected.", "reduction_pct": 5})

    return risks


def _build_facts_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Build a risk-facts dict from the report (mirrors engine/planner._risk_facts but standalone)."""
    jira = report.get("jira", {})
    github = report.get("github", {})
    cicd = report.get("cicd", {})
    blocked_details = jira.get("blocked_details", [])
    return {
        "blocked_over_4d": sum(1 for item in blocked_details if (item.get("over_4d_by_hours") or 0) > 0),
        "blocked_total": jira.get("blocked", 0),
        "pending_review_over_48h": github.get("pending_review_over_48h", 0),
        "pending_reviews": github.get("pending_reviews", 0),
        "build_failures": cicd.get("build_failures", 0),
        "last_build": cicd.get("last_build", "unknown"),
        "uat_state": cicd.get("environments", {}).get("uat", "unknown"),
        "prod_state": cicd.get("environments", {}).get("prod", "unknown"),
        "sprint_completion_pct": report.get("sprint_completion_pct", 0),
    }


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def _build_recommendations(risks: List[Dict[str, Any]], facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    recs = []
    risk_to_action = {
        "blocked_over_4d": {"action_id": "blocker-escalation-001", "text": "Escalate long-standing blockers to Senior Stakeholders."},
        "build_failures": {"action_id": "ci-stabilize-001", "text": "Trigger CI/CD stabilization workflow."},
        "pending_review_over_48h": {"action_id": "review-sla-001", "text": "Enforce PR review SLAs via Slack bot."},
        "uat_state_risky": {"action_id": "slack-alert-uat-failure-001", "text": "Alert QA team of UAT deployment risks."},
        "prod_state_risky": {"action_id": "slack-alert-prod-failure-001", "text": "Immediate production deployment health check."},
    }
    for r in risks:
        if r.get("risk_type") in risk_to_action:
            info = risk_to_action[r["risk_type"]]
            recs.append({"description": r.get("description"), "recommended_action": info["text"], "action_id": info["action_id"], "severity": r.get("severity")})
    if not recs:
        recs.append({"description": "No immediate critical risks found.", "recommended_action": "Maintain current velocity and monitor Slack for signals.", "action_id": "status-monitor-001", "severity": "low"})
    return recs


# ---------------------------------------------------------------------------
# Sprint plan
# ---------------------------------------------------------------------------

def _build_sprint_plan(adjusted_velocity: float) -> Dict[str, Any]:
    return {"recommended_sp": round(adjusted_velocity * 0.8, 1), "buffer_allocation": 0.2}


# ---------------------------------------------------------------------------
# Scenarios (AI-enriched)
# ---------------------------------------------------------------------------

def _call_groq_for_intelligence_narrative(base_velocity: float, risks: List[Dict[str, Any]], remaining_sp: float) -> Dict[str, Any]:
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_model = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct").strip()
    if not groq_api_key:
        return {}

    system_prompt = """You are a Strategic Delivery Coach. Analyze the provided sprint data and generate exactly THREE scenarios (Optimistic, Realistic, Pessimistic).
For each scenario, provide:
1. 'narrative': A concise (1-2 sentences) strategic insight.
2. 'velocity_factor': A float multiplier for the base velocity.

Your output MUST be a valid JSON object with the following structure:
{
  "optimistic": {"narrative": "...", "velocity_factor": 1.2},
  "realistic": {"narrative": "...", "velocity_factor": 0.9},
  "pessimistic": {"narrative": "...", "velocity_factor": 0.6}
}
"""
    user_message = f"Base Velocity: {base_velocity}\nRemaining Story Points: {remaining_sp}\nIdentified Risks: {json.dumps(risks)}"

    try:
        if _GROQ_SDK_AVAILABLE and _GroqClient:
            client = _GroqClient(api_key=groq_api_key)
            response = client.chat.completions.create(
                model=groq_model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}],
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        else:
            payload = {"model": groq_model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.4, "response_format": {"type": "json_object"}}
            req = urllib_request.Request("https://api.groq.com/openai/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}, method="POST")
            with urllib_request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return json.loads(body["choices"][0]["message"]["content"])
    except Exception as e:
        logger.error(f"Groq narrative generation failed: {e}")
        return {}


def _build_scenarios(base_velocity: float, risks: List[Dict[str, Any]], remaining_sp: float) -> Dict[str, Any]:
    ai_scenarios = _call_groq_for_intelligence_narrative(base_velocity, risks, remaining_sp)
    total_red = sum(r.get("reduction_pct", 0) for r in risks) / 100.0
    defaults = {
        "optimistic": {"factor": 1.2, "narrative": "Predicted based on ideal conditions and resolution of all blockers."},
        "realistic": {"factor": max(0.5, 1 - total_red), "narrative": "Predicted based on current risk profile and historical trends."},
        "pessimistic": {"factor": min(0.4, (1 - total_red) * 0.7), "narrative": "Predicted based on worst-case scenario with additional unforeseen delays."},
    }

    def format_scenario(key):
        ai_data = ai_scenarios.get(key, {})
        vel_factor = ai_data.get("velocity_factor", defaults[key]["factor"])
        narrative = ai_data.get("narrative", defaults[key]["narrative"])
        vel = base_velocity * vel_factor
        sprints = remaining_sp / vel if vel > 0 else 99.0
        return {"sprints": round(sprints, 1), "velocity": round(vel, 1), "narrative": narrative}

    return {"optimistic": format_scenario("optimistic"), "realistic": format_scenario("realistic"), "pessimistic": format_scenario("pessimistic")}


# ---------------------------------------------------------------------------
# Next sprint prediction
# ---------------------------------------------------------------------------

def _predict_next_sprint(report: Dict[str, Any], adjusted_velocity: float) -> Dict[str, Any]:
    jira = report.get("jira", {})
    sprint_metrics = jira.get("sprint_metrics", {})
    issues_by_sprint = jira.get("issues", {})

    sprint_mvp_pairs = []
    sprint_name_map = {}
    for s_name in sprint_metrics.keys():
        if s_name == "Backlog":
            continue
        nums = re.findall(r'\d+', s_name)
        if len(nums) >= 2:
            s_num, m_num = int(nums[0]), int(nums[1])
        elif len(nums) == 1:
            s_num, m_num = int(nums[0]), int(nums[0]) + 1
        else:
            continue
        sprint_mvp_pairs.append((s_num, m_num))
        sprint_name_map[s_num] = s_name

    if sprint_mvp_pairs:
        sprint_mvp_pairs.sort(key=lambda x: x[0])
        last_sprint_num, last_mvp_num = sprint_mvp_pairs[-1]
        next_sprint_name = f"Sprint {last_sprint_num + 1} - MVP {last_mvp_num + 1}"
    else:
        last_sprint_num, last_mvp_num = 5, 6
        next_sprint_name = "Sprint 6 - MVP 7"

    sprint_velocity_history = []
    for s_name, m in sprint_metrics.items():
        if s_name == "Backlog":
            continue
        pts_done = float(m.get("points_done", 0) or 0)
        pts_total = float(m.get("points_total", 0) or 0)
        sprint_velocity_history.append((s_name, pts_total, pts_done))
    sprint_velocity_history.sort(key=lambda e: int(re.findall(r'\d+', e[0])[0]) if re.findall(r'\d+', e[0]) else 0)

    sp_total_list = [t for _, t, _ in sprint_velocity_history]
    sp_done_list = [d for _, _, d in sprint_velocity_history]

    trend_parts = []
    if sp_total_list:
        avg_sp_assigned = sum(sp_total_list) / len(sp_total_list)
        avg_sp_done = sum(sp_done_list) / len(sp_done_list)
        trend_parts.append("SP assigned per sprint: " + ", ".join(f"{name}={total:.0f}SP (done {done:.0f})" for name, total, done in sprint_velocity_history))
        trend_parts.append(f"Avg SP assigned/sprint: {avg_sp_assigned:.1f}")
        trend_parts.append(f"Avg SP done/sprint: {avg_sp_done:.1f}")
        if len(sp_total_list) >= 2:
            recent_total = sp_total_list[-1]
            prev_avg_total = sum(sp_total_list[:-1]) / len(sp_total_list[:-1])
            trend_parts.append("Sprint scope trend: " + ("INCREASING" if recent_total > prev_avg_total * 1.1 else "DECREASING" if recent_total < prev_avg_total * 0.9 else "STABLE"))
    trend_summary = " | ".join(trend_parts) if trend_parts else "Insufficient history for trend."

    sprint_weeks = 1
    if sp_total_list:
        avg_sp_assigned = sum(sp_total_list) / len(sp_total_list)
        capacity = round(max(5.0, avg_sp_assigned * 0.85), 1)
    else:
        avg_sp_assigned = 0.0
        capacity = round(max(5.0, adjusted_velocity * 0.85), 1)

    backlog_items = issues_by_sprint.get("Backlog", [])
    DONE_STATUSES = {"done", "completed", "closed", "resolved"}
    TYPE_ORDER = {"bug": 0, "story": 1, "task": 2, "feature": 3, "request": 4}
    active_backlog = [i for i in backlog_items if str(i.get("status", "")).lower() not in DONE_STATUSES]

    def _pts(item): return float(item.get("points") or 0)
    def _type_rank(item): return TYPE_ORDER.get(str(item.get("type") or item.get("issuetype") or "task").lower(), 5)

    sorted_backlog = sorted(active_backlog, key=lambda x: (_type_rank(x), -_pts(x)))
    zero_pt_items = [i for i in sorted_backlog if _pts(i) == 0]

    selected = []
    allocated_pts = 0.0
    type_breakdown: Dict[str, int] = {}
    selected_ids: set = set()

    def _add_item(item):
        nonlocal allocated_pts
        issue_type = str(item.get("type") or "Task")
        pts = _pts(item)
        selected.append({"id": item.get("id"), "title": item.get("title", ""), "type": issue_type, "points": pts, "status": item.get("status"), "assignee": item.get("assignee") or "Unassigned", "complexity": ("Large (7+ SP)" if pts > 6 else "Medium (4-6 SP)" if pts > 3 else "Small (1-3 SP)" if pts > 0 else "Unpointed")})
        allocated_pts += pts
        type_breakdown[issue_type] = type_breakdown.get(issue_type, 0) + 1
        selected_ids.add(item.get("id"))

    for item in sorted_backlog:
        pts = _pts(item)
        if pts == 0:
            continue
        if pts <= capacity * 1.1 - allocated_pts:
            _add_item(item)

    if allocated_pts < capacity * 0.5 and zero_pt_items:
        for item in zero_pt_items[:2]:
            if item.get("id") not in selected_ids:
                _add_item(item)

    note = (
        f"Predicted sprint: {next_sprint_name} "
        f"(naming pattern: Sprint N - MVP N+1, last sprint was Sprint {last_sprint_num} - MVP {last_mvp_num}). "
        f"Sprint duration: {sprint_weeks} week(s). "
        f"Recommended capacity: {capacity} SP "
        f"(based on avg SP assigned per sprint {avg_sp_assigned:.1f} x 0.85 planning buffer; "
        f"avg SP done per sprint is {round(sum(sp_done_list)/len(sp_done_list),1) if sp_done_list else 0}). "
        f"Issue selection: Bugs prioritised first, then Stories, then Tasks; "
        f"within each type higher points first; any number of issues per point value allowed. "
        f"Trend: {trend_summary}."
    )

    return {
        "next_sprint_name": next_sprint_name,
        "sprint_weeks": sprint_weeks,
        "recommended_capacity_sp": capacity,
        "allocated_sp": round(allocated_pts, 1),
        "issue_count": len(selected),
        "issues": selected,
        "type_breakdown": type_breakdown,
        "velocity_trend": {
            "per_sprint": [{"sprint": name, "sp_assigned": total, "sp_done": done, "completion_pct": round((done / total * 100) if total > 0 else 0, 1)} for name, total, done in sprint_velocity_history],
            "avg_sp_assigned": round(sum(sp_total_list) / len(sp_total_list), 1) if sp_total_list else 0,
            "avg_sp_done": round(sum(sp_done_list) / len(sp_done_list), 1) if sp_done_list else 0,
            "trend_summary": trend_summary,
        },
        "note": note,
    }


# ---------------------------------------------------------------------------
# Sprint-over-sprint trend
# ---------------------------------------------------------------------------

def _compute_sprint_over_sprint_trend(report: Dict[str, Any]) -> Dict[str, Any]:
    jira = report.get("jira", {})
    sprint_metrics = jira.get("sprint_metrics", {})
    entries = []
    for s_name, m in sprint_metrics.items():
        if s_name == "Backlog":
            continue
        pts_total = float(m.get("points_total", 0) or 0)
        pts_done = float(m.get("points_done", 0) or 0)
        total_issues = int(m.get("total", 0) or 0)
        completed_issues = int(m.get("completed", 0) or 0)
        blocked_issues = int(m.get("blocked", 0) or 0)
        sp_rate = round((pts_done / pts_total * 100) if pts_total > 0 else 0, 1)
        issue_rate = round((completed_issues / total_issues * 100) if total_issues > 0 else 0, 1)
        nums = re.findall(r'\d+', s_name)
        order = int(nums[0]) if nums else 0
        entries.append({"sprint": s_name, "order": order, "sp_assigned": pts_total, "sp_done": pts_done, "sp_completion_rate": sp_rate, "total_issues": total_issues, "completed_issues": completed_issues, "blocked_issues": blocked_issues, "issue_completion_rate": issue_rate})

    entries.sort(key=lambda x: x["order"])
    for i, e in enumerate(entries):
        if i == 0:
            e["sp_rate_delta"] = None
            e["sp_done_delta"] = None
            e["direction"] = "baseline"
        else:
            prev = entries[i - 1]
            sp_delta = round(e["sp_completion_rate"] - prev["sp_completion_rate"], 1)
            e["sp_rate_delta"] = sp_delta
            e["sp_done_delta"] = round(e["sp_done"] - prev["sp_done"], 1)
            e["direction"] = "improving" if sp_delta > 5 else "declining" if sp_delta < -5 else "stable"

    rates = [e["sp_completion_rate"] for e in entries]
    if len(rates) >= 4:
        mid = len(rates) // 2
        first_half_avg = sum(rates[:mid]) / mid
        second_half_avg = sum(rates[mid:]) / (len(rates) - mid)
        if second_half_avg > first_half_avg * 1.1:
            trajectory = "IMPROVING"
        elif second_half_avg < first_half_avg * 0.9:
            trajectory = "RECOVERING" if len(rates) >= 2 and rates[-1] > rates[-2] else "DECLINING"
        else:
            trajectory = "STABLE"
    elif len(rates) >= 2:
        trajectory = "IMPROVING" if rates[-1] > rates[-2] * 1.05 else "DECLINING" if rates[-1] < rates[-2] * 0.95 else "STABLE"
    else:
        trajectory = "INSUFFICIENT DATA"

    best = max(entries, key=lambda x: x["sp_completion_rate"]) if entries else None
    worst = min(entries, key=lambda x: x["sp_completion_rate"]) if entries else None
    improving_count = sum(1 for e in entries if e.get("direction") == "improving")
    declining_count = sum(1 for e in entries if e.get("direction") == "declining")
    stable_count = sum(1 for e in entries if e.get("direction") == "stable")

    verdict = (
        f"Overall trajectory: {trajectory}. "
        f"{improving_count} sprint(s) improved, {declining_count} declined, {stable_count} held steady. "
        + (f"Best sprint: {best['sprint']} ({best['sp_completion_rate']}% SP done). " if best else "")
        + (f"Worst sprint: {worst['sprint']} ({worst['sp_completion_rate']}% SP done)." if worst else "")
    )

    return {
        "trajectory": trajectory,
        "sprints": entries,
        "improving_count": improving_count,
        "declining_count": declining_count,
        "stable_count": stable_count,
        "best_sprint": {"name": best["sprint"], "sp_completion_rate": best["sp_completion_rate"]} if best else None,
        "worst_sprint": {"name": worst["sprint"], "sp_completion_rate": worst["sp_completion_rate"]} if worst else None,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Weekly executive summary
# ---------------------------------------------------------------------------

def _generate_executive_summary(
    velocity: Dict[str, Any],
    adjusted: Dict[str, Any],
    forecast: Dict[str, Any],
    backlog: Dict[str, Any],
    next_sprint: Dict[str, Any],
    retrospectives: Dict[str, Any],
    stale_issues: Dict[str, Any],
    pr_quality: Dict[str, Any],
    risk_score: Dict[str, Any],
    health_score: Dict[str, Any],
) -> Dict[str, Any]:
    avg_vel = velocity.get("average_velocity", 0)
    trend = velocity.get("trend", "stable").upper()
    adj_vel = adjusted.get("adjusted_velocity", 0)
    remaining_sp = backlog.get("remaining_story_points", 0)
    p50 = forecast.get("p50_sprints", "N/A")
    p85 = forecast.get("p85_sprints", "N/A")
    next_sprint_name = next_sprint.get("next_sprint_name", "Next Sprint")
    next_capacity = next_sprint.get("recommended_capacity_sp", 0)
    avg_sp_rate = retrospectives.get("avg_sp_completion_rate_pct", 0)
    retro_verdict = retrospectives.get("summary", "")
    stale_count = stale_issues.get("total_stale_issues", 0)
    stale_sp = stale_issues.get("total_stale_sp", 0)
    pr_rework = pr_quality.get("rework_rate_pct", 0) if isinstance(pr_quality, dict) else 0
    pr_pending = pr_quality.get("pending_over_48h", 0) if isinstance(pr_quality, dict) else 0
    d_score = risk_score.get("score", 0)
    d_label = risk_score.get("label", "")
    h_score = health_score.get("score", 0)
    h_grade = health_score.get("grade", "")

    lines = [
        "## Weekly Delivery Executive Summary",
        "",
        f"### Delivery Health: {d_label} ({d_score}/100) | Team Health: {h_grade} ({h_score}/100)",
        "",
        "**Velocity & Forecast**",
        f"- Avg SP assigned per sprint: {avg_vel:.0f} | Trend: {trend}",
        f"- Risk-adjusted velocity: {adj_vel:.1f} SP/sprint",
        f"- Remaining scope: {remaining_sp:.0f} SP",
        f"- Forecast to completion: {p50} sprints (realistic) / {p85} sprints (conservative)",
        "",
        "**Sprint Delivery**",
        f"- Avg SP completion rate across all sprints: {avg_sp_rate}%",
        f"- Verdict: {retro_verdict}",
        f"- Carry-over risk: {stale_count} unresolved issues ({stale_sp:.0f} SP) stuck in past sprints",
        "",
        "**Code Quality**",
        f"- PR rework rate: {pr_rework}% | PRs pending review >48h: {pr_pending}",
        f"- PR quality label: {pr_quality.get('quality_label', 'N/A') if isinstance(pr_quality, dict) else 'N/A'}",
        "",
        "**Next Sprint Plan**",
        f"- Predicted sprint: {next_sprint_name}",
        f"- Recommended capacity: {next_capacity} SP ({next_sprint.get('issue_count', 0)} issues)",
        "",
        "**Top Risk Factors**",
    ]
    for b in risk_score.get("breakdown", [])[:3]:
        lines.append(f"- {b['factor']}: -{b['deduction']} pts — {b['detail']}")
    if not risk_score.get("breakdown"):
        lines.append("- No major risk factors detected.")

    return {
        "text": "\n".join(lines),
        "delivery_risk_score": d_score,
        "delivery_risk_label": d_label,
        "team_health_score": h_score,
        "team_health_grade": h_grade,
        "forecast_p50_sprints": p50,
        "forecast_p85_sprints": p85,
        "next_sprint_name": next_sprint_name,
        "generated_note": "Auto-generated executive summary — no LLM required.",
    }
