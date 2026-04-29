# -*- coding: utf-8 -*-
"""
intelligence/risk_score.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Delivery Risk Score (0-100) and Team Health Score (0-100).
Both are fully deterministic — no LLM required.
"""

from typing import Any, Dict, List


def _compute_delivery_risk_score(
    retrospectives: Dict[str, Any],
    stale_issues: Dict[str, Any],
    pr_quality: Dict[str, Any],
    risks: List[Dict[str, Any]],
    velocity_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Single 0-100 Delivery Risk Score for daily stakeholder monitoring.
    100 = no risk, 0 = critical failure. Deductions are weighted and capped.
    """
    score = 100.0
    breakdown = []

    # 1. SP completion rate (weight: 35 pts)
    avg_sp_rate = retrospectives.get("avg_sp_completion_rate_pct", 100)
    if avg_sp_rate < 30:
        deduction = 35
        breakdown.append({"factor": "SP Completion Rate", "deduction": deduction, "detail": f"Avg {avg_sp_rate}% — Critical under-delivery"})
    elif avg_sp_rate < 60:
        deduction = 20
        breakdown.append({"factor": "SP Completion Rate", "deduction": deduction, "detail": f"Avg {avg_sp_rate}% — Below target"})
    elif avg_sp_rate < 80:
        deduction = 8
        breakdown.append({"factor": "SP Completion Rate", "deduction": deduction, "detail": f"Avg {avg_sp_rate}% — Slightly below target"})
    else:
        deduction = 0
    score -= deduction

    # 2. Stale / carry-over issues (weight: 20 pts)
    stale_count = stale_issues.get("total_stale_issues", 0)
    critical_stale = stale_issues.get("critical_count", 0)
    stale_deduction = min(20, stale_count * 1.2 + critical_stale * 3)
    if stale_deduction > 0:
        breakdown.append({"factor": "Stale Issues", "deduction": round(stale_deduction, 1), "detail": f"{stale_count} carry-over issues, {critical_stale} blocked"})
    score -= stale_deduction

    # 3. PR rework / quality (weight: 20 pts)
    rework_rate = pr_quality.get("rework_rate_pct", 0) if isinstance(pr_quality, dict) else 0
    pending_48h = pr_quality.get("pending_over_48h", 0) if isinstance(pr_quality, dict) else 0
    pr_deduction = min(20, rework_rate * 0.3 + pending_48h * 5)
    if pr_deduction > 0:
        breakdown.append({"factor": "PR Quality / Review Delays", "deduction": round(pr_deduction, 1), "detail": f"Rework rate {rework_rate}%, {pending_48h} PRs pending >48h"})
    score -= pr_deduction

    # 4. Active risk signals (weight: 15 pts)
    risk_deduction = min(15, len(risks) * 5)
    if risk_deduction > 0:
        breakdown.append({"factor": "Active Risk Signals", "deduction": risk_deduction, "detail": f"{len(risks)} risk(s) detected"})
    score -= risk_deduction

    # 5. Velocity trend (weight: 10 pts)
    if velocity_analysis.get("trend") == "declining":
        breakdown.append({"factor": "Velocity Trend", "deduction": 10, "detail": "Sprint scope / velocity is declining"})
        score -= 10

    score = round(max(0.0, min(100.0, score)), 1)
    label = "Healthy" if score >= 80 else "Moderate Risk" if score >= 60 else "High Risk" if score >= 40 else "Critical"
    color = "green" if score >= 80 else "yellow" if score >= 60 else "orange" if score >= 40 else "red"

    return {
        "score": score,
        "label": label,
        "color": color,
        "max_score": 100,
        "breakdown": breakdown,
        "interpretation": (
            f"Delivery Risk Score: {score}/100 ({label}). "
            + (f"Key concerns: {'; '.join(b['factor'] for b in breakdown[:3])}." if breakdown else "No major risks detected.")
        ),
    }


def _compute_team_health_score(
    retrospectives: Dict[str, Any],
    pr_quality: Dict[str, Any],
    stale_issues: Dict[str, Any],
    team_capacity: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Composite Team Health Score (0-100):
    SP Delivery (40) + Code Quality (25) + Issue Hygiene (20) + Capacity Balance (15).
    """
    components = {}

    # Pillar 1: SP Delivery (max 40)
    avg_sp_rate = retrospectives.get("avg_sp_completion_rate_pct", 0)
    sp_score = round(min(40, avg_sp_rate * 0.4), 1)
    components["sp_delivery"] = {"score": sp_score, "max": 40, "detail": f"Avg SP completion rate: {avg_sp_rate}%"}

    # Pillar 2: Code Quality (max 25)
    q_score = pr_quality.get("quality_score", 50) if isinstance(pr_quality, dict) else 50
    code_score = round(min(25, q_score * 0.25), 1)
    components["code_quality"] = {"score": code_score, "max": 25, "detail": f"PR quality score: {q_score}/100"}

    # Pillar 3: Issue Hygiene (max 20)
    stale_count = stale_issues.get("total_stale_issues", 0)
    total_issues_est = max(1, stale_count + 10)
    stale_ratio = stale_count / total_issues_est
    hygiene_score = round(max(0, 20 * (1 - min(1, stale_ratio * 2))), 1)
    components["issue_hygiene"] = {"score": hygiene_score, "max": 20, "detail": f"{stale_count} stale issues detected"}

    # Pillar 4: Capacity Balance (max 15)
    members = team_capacity.get("members", [])
    overcommitted = sum(1 for m in members if m.get("load_status") == "Overcommitted")
    total_members = max(1, len(members))
    balance_score = round(15 * (1 - overcommitted / total_members), 1)
    components["capacity_balance"] = {"score": balance_score, "max": 15, "detail": f"{overcommitted}/{total_members} member(s) overcommitted"}

    total = round(sum(c["score"] for c in components.values()), 1)
    grade = "Excellent" if total >= 85 else "Good" if total >= 70 else "Needs Attention" if total >= 50 else "Critical"

    return {
        "score": total,
        "max_score": 100,
        "grade": grade,
        "components": components,
        "interpretation": (
            f"Team Health Score: {total}/100 ({grade}). "
            f"SP Delivery: {components['sp_delivery']['score']}/40 | "
            f"Code Quality: {components['code_quality']['score']}/25 | "
            f"Issue Hygiene: {components['issue_hygiene']['score']}/20 | "
            f"Capacity: {components['capacity_balance']['score']}/15."
        ),
    }
