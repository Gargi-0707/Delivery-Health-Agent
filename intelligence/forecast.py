# -*- coding: utf-8 -*-
"""
intelligence/forecast.py
~~~~~~~~~~~~~~~~~~~~~~~~
Sprint velocity analysis, backlog snapshot, risk-adjusted velocity,
and Monte Carlo probabilistic forecast (P50 / P85).
"""

import math
import random
import re
import statistics
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Velocity history
# ---------------------------------------------------------------------------

def _extract_velocity_history(memory_state: Dict[str, Any]) -> List[float]:
    """Read sprint_completion_pct from past memory runs (fallback signal)."""
    history = []
    for run in (memory_state or {}).get("runs", []):
        facts = run.get("observed_facts", {})
        if "sprint_completion_pct" in facts:
            history.append(float(facts["sprint_completion_pct"]))
    return history


def _extract_assigned_sp_history(report: Dict[str, Any]) -> List[float]:
    """Read points_total (SP planned) per sprint from sprint_metrics — primary signal."""
    jira = report.get("jira", {})
    sprint_metrics = jira.get("sprint_metrics", {})
    entries = []
    for s_name, m in sprint_metrics.items():
        if s_name == "Backlog":
            continue
        pts_total = float(m.get("points_total", 0) or 0)
        if pts_total > 0:
            nums = re.findall(r'\d+', s_name)
            sprint_order = int(nums[0]) if nums else 0
            entries.append((sprint_order, pts_total))
    entries.sort(key=lambda x: x[0])
    return [pts for _, pts in entries]


def _compute_velocity_analysis(history: List[float]) -> Dict[str, Any]:
    if not history:
        return {"average_velocity": 0.0, "trend": "stable"}

    avg_velocity = sum(history) / len(history)
    trend = "stable"
    if len(history) >= 2:
        last_val = history[-1]
        prev_avg = sum(history[:-1]) / len(history[:-1])
        if last_val > prev_avg * 1.1:
            trend = "increasing"
        elif last_val < prev_avg * 0.9:
            trend = "declining"

    return {"average_velocity": round(float(avg_velocity), 2), "trend": trend}


# ---------------------------------------------------------------------------
# Backlog snapshot
# ---------------------------------------------------------------------------

def _extract_backlog_snapshot(report: Dict[str, Any]) -> Dict[str, Any]:
    jira_data = report.get("jira", {})
    total_issues = jira_data.get("total_tasks", 0)
    completed_issues = jira_data.get("completed", 0)
    remaining_tasks = max(0, total_issues - completed_issues)

    sprint_metrics = jira_data.get("sprint_metrics", {})
    remaining_sp = backlog_sp = 0
    for s_name, m in sprint_metrics.items():
        pts_total = m.get("points_total", 0) or 0
        pts_done = m.get("points_done", 0) or 0
        if s_name == "Backlog":
            backlog_sp += max(0, pts_total - pts_done)
        else:
            remaining_sp += max(0, pts_total - pts_done)

    total_remaining_sp = remaining_sp + backlog_sp
    weighted_work = total_remaining_sp if total_remaining_sp > 0 else remaining_tasks * 3

    return {
        "remaining_story_points": total_remaining_sp,
        "active_sprint_remaining_sp": remaining_sp,
        "backlog_sp": backlog_sp,
        "remaining_tasks": remaining_tasks,
        "weighted_remaining_work": weighted_work,
    }


# ---------------------------------------------------------------------------
# Risk adjustment
# ---------------------------------------------------------------------------

def _adjust_velocity(
    base_velocity: float,
    risks: List[Dict[str, Any]],
    memory_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if base_velocity <= 0:
        return {"adjusted_velocity": 0, "adjustment_reasons": []}

    total_reduction = 0.0
    reasons: List[str] = []
    history_runs = (memory_state or {}).get("runs", [])

    for r in risks:
        risk_type = r.get("risk_type")
        baseline_pct = r.get("reduction_pct", 0) / 100.0
        historical_impacts = []
        for run in history_runs:
            facts = run.get("observed_facts", {})
            if facts.get(risk_type, 0) > 0 or (risk_type == "uat_state_risky" and facts.get("uat_state") != "success"):
                completion = facts.get("sprint_completion_pct", 100)
                historical_impacts.append((100 - completion) / 100.0)

        impact_pct = (baseline_pct * 0.5 + sum(historical_impacts) / len(historical_impacts) * 0.5) \
            if historical_impacts else baseline_pct

        if impact_pct > 0:
            total_reduction += impact_pct
            reasons.append(f"{r.get('description', '')} (Impact: -{impact_pct*100:.1f}%)")

    if total_reduction > 0.5:
        total_reduction = 0.5
        reasons.append("Combined risk reduction capped at 50%.")

    adjusted = max(0.0, base_velocity * (1 - total_reduction))
    return {"adjusted_velocity": round(adjusted, 2), "adjustment_reasons": reasons}


# ---------------------------------------------------------------------------
# Monte Carlo forecast
# ---------------------------------------------------------------------------

def _compute_forecast(
    adjusted_velocity: float,
    remaining_sp: float,
    history: List[float] | None = None,
) -> Dict[str, Any]:
    if adjusted_velocity <= 0 or remaining_sp <= 0:
        return {"p50_sprints": 99.0, "p85_sprints": 99.0, "confidence": "low", "method": "deterministic_fallback"}

    std_dev = adjusted_velocity * 0.15
    if history and len(history) >= 3:
        try:
            std_dev = statistics.stdev(history)
        except statistics.StatisticsError:
            pass

    sim_results = []
    for _ in range(1000):
        sim_vel = max(0.1, random.gauss(adjusted_velocity, std_dev))
        sim_results.append(remaining_sp / sim_vel)
    sim_results.sort()

    return {
        "p50_sprints": round(sim_results[500], 1),
        "p85_sprints": round(sim_results[850], 1),
        "p50_weeks": round(sim_results[500] * 2, 1),
        "p85_weeks": round(sim_results[850] * 2, 1),
        "confidence": "high" if len(history or []) >= 5 else "medium",
        "method": "monte_carlo_1000_sims",
    }
