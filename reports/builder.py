# -*- coding: utf-8 -*-
"""
reports/builder.py
~~~~~~~~~~~~~~~~~~
Top-level report assembly:
  - generate_weekly_report()  — the main pipeline orchestrator
  - _build_premium_report_text() — executive text summary
"""

import json
from datetime import datetime, timezone, timedelta

from core.config import GROQ_API_KEY, GROQ_MODEL, LATEST_FULL_REPORT_FILE
from core.logging import log_event


def _build_premium_report_text(report_output: dict) -> str:
    """
    Build the 'Premium' executive-level text block that is appended to insights.
    Relies on agent output for coaching, action queue, and execution results.
    """
    final_report = report_output["report"]
    agent_output = report_output.get("agent")
    if not agent_output:
        return "Premium Report: No agent data available."

    learning = (agent_output or {}).get("learning", {})
    coaching = (learning or {}).get("ai_coaching", {}) or {}
    execution = agent_output.get("execution", {})

    score = coaching.get("health_score", 0)
    trend_val = coaching.get("health_trend", "stable")
    status_label = "HEALTHY" if score > 80 else "AT RISK" if score > 50 else "CRITICAL"

    completion = final_report.get("sprint_completion_pct", 0)
    overall_comp = final_report.get("overall_project_progress_pct", 0)

    blocked_items = [
        f"  * [BLOCKED] {b['id']} (Assignee: {b.get('assignee', 'Unassigned')}) - {b.get('blocked_for_hours', 0)}h"
        for b in final_report["jira"].get("blocked_details", [])
    ] or ["  * [OK] No active blockers"]

    github = final_report.get("github", {})
    pr_bottlenecks = github.get("pending_review_over_48h", 0)

    action_queue = agent_output.get("action_queue", [])
    executed_items = []
    for action in action_queue[:3]:
        status_items = execution.get("executed_actions", [])
        exec_status = next(
            (e.get("status") for e in status_items if e.get("action_id") == action["action_id"]),
            "pending",
        )
        label = "[OK]" if exec_status == "executed" else "[DRY]" if exec_status == "dry_run" else "[WAIT]"
        executed_items.append(f"  {label} {action.get('objective', action['action_id'])}")

    report = [
        "============================================================",
        "*** PREMIUM AI EXECUTIVE SUMMARY (Version 2.0) ***",
        "============================================================",
        "",
        f"[HEALTH]  STATUS: {status_label} ({score}/100) | TREND: {trend_val}",
        f"[STATS]   Active Sprint Completion: {completion:.2f}% (Current Goals)",
        f"[STATS]   Overall Project Progress: {overall_comp:.2f}% (Total Backlog)",
        "",
        "[STRATEGY] AI Strategic Coaching:",
        f"  \"{coaching.get('coaching_message', 'No message available.')}\"",
        "",
        "[RISKS] Top Delivery Risks:",
        "\n".join(blocked_items),
        f"  * [WARN] BOTTLENECK: {pr_bottlenecks} PRs pending review > 48h",
        f"  * [BUILD] Last build: {final_report.get('cicd', {}).get('last_build', 'unknown')}",
        "",
        "[FORECAST] Probabilistic Delivery:",
        f"  * P50 (Realistic): {final_report.get('intelligence', {}).get('forecast', {}).get('p50_sprints', 'N/A')} Sprints",
        f"  * P85 (Safe Date): {final_report.get('intelligence', {}).get('forecast', {}).get('p85_sprints', 'N/A')} Sprints",
        f"  * Method: {final_report.get('intelligence', {}).get('forecast', {}).get('method', 'deterministic')}",
        "",
        "[INSIGHT] Strategic Scenarios:",
        f"  * Optimistic: {final_report.get('intelligence', {}).get('scenarios', {}).get('optimistic', {}).get('narrative', 'N/A')}",
        f"  * Realistic:  {final_report.get('intelligence', {}).get('scenarios', {}).get('realistic', {}).get('narrative', 'N/A')}",
        f"  * Pessimistic: {final_report.get('intelligence', {}).get('scenarios', {}).get('pessimistic', {}).get('narrative', 'N/A')}",
        "",
        "[ACTIONS] Agentic Interventions (ActAgent):",
        "\n".join(executed_items),
        "",
        "[INSIGHTS] Slack Intelligence (AI Summary):",
        f"  {final_report.get('slack', {}).get('week_summary', 'No summary available.')[:300]}...",
        "",
        "[SYSTEM] Ecosystem Status:",
        f"  Jira: {final_report['jira'].get('total_tasks', 0)} items | GitHub: {github.get('total_prs', 0)} PRs | CI/CD: {final_report.get('cicd', {}).get('last_build', 'N/A')}",
        "============================================================",
    ]

    return "\n".join(report)


def generate_weekly_report(
    include_ai_insights: bool = True,
    agent_mode: bool = False,
    agent_execute: bool = False,
) -> dict:
    """
    Main pipeline entry point.

    Runs: Observe → Analyze → Intelligence → Decide → Act → Learn
    Returns a dict with keys: report, insights, agent.
    """
    # Import here to avoid circular imports at module level
    from core.config import validate_config, runtime_support_status
    from agents.observe import ObserveAgent
    from agents.analyzer import AnalyzerAgent
    from agents.decide import DecideAgent
    from agents.act import ActAgent
    from agents.learn import LearnAgent
    from engine.memory import _load_memory_state
    from intelligence.runner import run_delivery_intelligence

    validate_config()
    runtime = runtime_support_status()

    observations = ObserveAgent.run()
    analysis = AnalyzerAgent.run(observations, include_ai_insights, runtime)
    
    # Calculate IST (UTC +5:30)
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    analysis["report"]["generated_at_utc"] = ist_now.strftime("%d-%b-%Y %I:%M %p IST")

    # High-level delivery intelligence
    memory_state = _load_memory_state()
    intelligence = run_delivery_intelligence(analysis["report"], memory_state)
    analysis["report"]["intelligence"] = intelligence

    agent = None
    executions = {"executed_actions": []}

    if agent_mode:
        decisions = DecideAgent.run(analysis)
        executions = ActAgent.run(
            decisions,
            execute_enabled=agent_execute,
            groq_api_key=GROQ_API_KEY,
            groq_model=GROQ_MODEL,
        )
        agent = LearnAgent.run(
            decisions,
            executions,
            execute_enabled=agent_execute,
            groq_api_key=GROQ_API_KEY,
            groq_model=GROQ_MODEL,
        )

    analysis["report"]["executed_actions"] = executions["executed_actions"]

    # ── Insights Assembly ──────────────────────────────────────────────────
    
    # 1. AI Insights (if requested and successful)
    ai_insights = analysis.get("insights")
    
    # 2. Deterministic Executive Summary (from Intelligence Engine)
    exec_summary = intelligence.get("executive_summary", {}).get("text", "")
    
    # 3. Premium/Agent Summary
    premium_text = _build_premium_report_text({"report": analysis["report"], "agent": agent})
    
    combined_insights = []
    if ai_insights:
        combined_insights.append("### AI ANALYST INSIGHTS\n" + ai_insights)
    
    if exec_summary:
        combined_insights.append(exec_summary)
    
    # Only show premium text if we actually have agent data OR if we are in agent mode
    if agent or agent_mode:
        combined_insights.append(premium_text)
    
    analysis["insights"] = "\n\n".join(combined_insights)

    # Persist the full report for the AI bot
    try:
        with open(LATEST_FULL_REPORT_FILE, "w", encoding="utf-8") as fh:
            json.dump(analysis["report"], fh, indent=2)
    except Exception as exc:
        log_event("error", "save_full_report_failed", error=str(exc))

    # 4. Final Data Assembly for Gmail/n8n
    report_highlights = {
        "health_score": intelligence.get("delivery_risk_score", {}),
        "team_health": intelligence.get("team_health_score", {}),
        "velocity_and_forecast": {
            "avg_velocity": intelligence.get("velocity", {}).get("average_velocity"),
            "velocity_trend": intelligence.get("velocity", {}).get("trend"),
            "adjusted_velocity": intelligence.get("adjusted_velocity", {}).get("adjusted_velocity"),
            "remaining_sp": intelligence.get("backlog", {}).get("remaining_story_points"),
            "p50_sprints": intelligence.get("forecast", {}).get("p50_sprints"),
            "p85_sprints": intelligence.get("forecast", {}).get("p85_sprints"),
        },
        "sprint_delivery": {
            "avg_completion_rate": intelligence.get("sprint_retrospectives", {}).get("avg_sp_completion_rate_pct"),
            "verdict": intelligence.get("sprint_retrospectives", {}).get("summary"),
            "carry_over_sp": intelligence.get("stale_issues", {}).get("total_stale_sp"),
        }
    }

    return {
        "report": analysis["report"],
        "insights": analysis["insights"],
        "agent": agent,
        "report_highlights": report_highlights
    }
