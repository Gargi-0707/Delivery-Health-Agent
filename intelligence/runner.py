# -*- coding: utf-8 -*-
"""
intelligence/runner.py
~~~~~~~~~~~~~~~~~~~~~~
Orchestrates all intelligence sub-modules and returns the structured
delivery intelligence JSON consumed by the rest of the pipeline.
"""

from typing import Any, Dict

from intelligence.forecast import (
    _extract_assigned_sp_history,
    _extract_velocity_history,
    _compute_velocity_analysis,
    _extract_backlog_snapshot,
    _adjust_velocity,
    _compute_forecast,
)
from intelligence.executive import (
    _build_facts_from_report,
    _evaluate_risks,
    _build_recommendations,
    _build_sprint_plan,
    _build_scenarios,
    _predict_next_sprint,
    _compute_sprint_over_sprint_trend,
    _generate_executive_summary,
)
from intelligence.team import (
    _compute_team_capacity,
    _compute_sprint_retrospectives,
    _compute_active_work_snapshot,
)
from intelligence.pr_quality import (
    _compute_pr_quality_metrics,
    _detect_stale_issues,
)
from intelligence.risk_score import (
    _compute_delivery_risk_score,
    _compute_team_health_score,
)


def run_delivery_intelligence(report: Dict[str, Any], memory_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Orchestrate all intelligence components and return a single structured dict.
    This is the only public function external modules should import.
    """
    # ── Velocity ──────────────────────────────────────────────────────────────
    history = _extract_assigned_sp_history(report)
    if not history:
        history = _extract_velocity_history(memory_state)

    analysis = _compute_velocity_analysis(history)
    backlog = _extract_backlog_snapshot(report)
    facts = _build_facts_from_report(report)
    risks = _evaluate_risks(report, facts)
    adjusted = _adjust_velocity(analysis["average_velocity"], risks, memory_state)

    # ── Forecast ──────────────────────────────────────────────────────────────
    forecast = _compute_forecast(adjusted["adjusted_velocity"], backlog["weighted_remaining_work"], history)
    next_sprint = _predict_next_sprint(report, adjusted["adjusted_velocity"])

    # ── Enterprise analytics ──────────────────────────────────────────────────
    team_capacity = _compute_team_capacity(report)
    retrospectives = _compute_sprint_retrospectives(report)
    pr_quality = _compute_pr_quality_metrics(report)
    stale_issues = _detect_stale_issues(report)

    # ── KPI dashboards ────────────────────────────────────────────────────────
    risk_score = _compute_delivery_risk_score(retrospectives, stale_issues, pr_quality, risks, analysis)
    health_score = _compute_team_health_score(retrospectives, pr_quality, stale_issues, team_capacity)
    exec_summary = _generate_executive_summary(
        analysis, adjusted, forecast, backlog,
        next_sprint, retrospectives, stale_issues,
        pr_quality, risk_score, health_score,
    )
    sprint_trend = _compute_sprint_over_sprint_trend(report)

    return {
        "velocity": analysis,
        "backlog": backlog,
        "risks": risks,
        "adjusted_velocity": adjusted,
        "forecast": forecast,
        "plan": _build_sprint_plan(adjusted["adjusted_velocity"]),
        "scenarios": _build_scenarios(analysis["average_velocity"], risks, backlog["remaining_story_points"]),
        "recommendations": _build_recommendations(risks, facts),
        "next_sprint_prediction": next_sprint,
        # Enterprise modules
        "team_capacity": team_capacity,
        "sprint_retrospectives": retrospectives,
        "pr_quality": pr_quality,
        "stale_issues": stale_issues,
        # KPI dashboards
        "delivery_risk_score": risk_score,
        "team_health_score": health_score,
        "executive_summary": exec_summary,
        "sprint_over_sprint_trend": sprint_trend,
        "active_work_snapshot": _compute_active_work_snapshot(report),
    }
