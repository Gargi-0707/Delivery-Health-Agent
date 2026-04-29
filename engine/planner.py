# -*- coding: utf-8 -*-
"""
engine/planner.py
~~~~~~~~~~~~~~~~~
The agentic decision planner:
  - _risk_facts()          — extract numeric risk signals from the report
  - _decide_actions()      — LLM-first, deterministic fallback
  - run_agentic_planner()  — multi-cycle orchestrator
"""

import json
import os
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from groq import Groq as _GroqClient
    _GROQ_SDK_AVAILABLE = True
except ImportError:
    _GroqClient = None
    _GROQ_SDK_AVAILABLE = False

from engine.catalog import (
    ACTION_CATALOG,
    _build_action_from_catalog,
    _sort_actions_by_priority,
)


# ---------------------------------------------------------------------------
# Risk facts extraction
# ---------------------------------------------------------------------------

def _risk_facts(final_report: dict) -> dict:
    """Extract numeric risk signals from the assembled report."""
    jira = final_report.get("jira", {})
    github = final_report.get("github", {})
    cicd = final_report.get("cicd", {})

    issues_by_sprint = jira.get("issues", {})
    backlog_count = len(issues_by_sprint.get("Backlog", []))
    sprint_metrics = jira.get("sprint_metrics", {})
    sprint_count = max(0, len(sprint_metrics) - (1 if "Backlog" in sprint_metrics else 0))

    return {
        "blocked_over_4d": sum(
            1 for item in jira.get("blocked_details", [])
            if (item.get("over_4d_by_hours") or 0) > 0
        ),
        "blocked_total": jira.get("blocked", 0),
        "backlog_count": backlog_count,
        "sprint_count": sprint_count,
        "pending_review_over_48h": github.get("pending_review_over_48h", 0),
        "pending_reviews": github.get("pending_reviews", 0),
        "build_failures": cicd.get("build_failures", 0),
        "last_build": cicd.get("last_build", "unknown"),
        "uat_state": cicd.get("environments", {}).get("uat", "unknown"),
        "prod_state": cicd.get("environments", {}).get("prod", "unknown"),
        "sprint_completion_pct": final_report.get("sprint_completion_pct", 0),
        "overall_project_progress_pct": final_report.get("overall_project_progress_pct", 0),
    }


# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

def _build_llm_system_prompt() -> str:
    catalog_lines = [
        f'  - action_id: "{aid}"\n'
        f'    description: {meta["description"]}\n'
        f'    trigger_condition: {meta["trigger_condition"]}\n'
        f'    priority: {meta["priority"]}'
        for aid, meta in ACTION_CATALOG.items()
    ]
    catalog_str = "\n".join(catalog_lines)

    return f"""You are the DecideAgent inside an AI Delivery Health System.
Your ONLY job is to analyse the provided sprint health facts and decide which interventions to trigger.

## ALLOWED ACTIONS (Action Catalog)
You MUST only choose action_ids from the following list. Any other action_id you invent will be REJECTED by the system.

{catalog_str}

## RESPONSE FORMAT
Respond with a JSON array of action objects. Each object MUST have exactly one field: "action_id" (string).
Example: [{{"action_id": "ci-stabilize-001"}}, {{"action_id": "review-sla-001"}}]

Rules:
- If NO risks are present, return [{{"action_id": "monitor-only-001"}}].
- Do NOT include any explanation, markdown, or extra text – ONLY the JSON array.
- Select ALL actions whose trigger conditions are met.
- Prioritise higher severity risks (P0 before P1 before P2).
"""


# ---------------------------------------------------------------------------
# Groq call + validation
# ---------------------------------------------------------------------------

def _call_groq_for_decisions(facts: dict, memory_feedback: dict, groq_api_key: str, groq_model: str) -> list:
    facts_str = json.dumps(facts, indent=2)
    memory_str = json.dumps(memory_feedback, indent=2)
    user_message = (
        f"## Current Sprint Health Facts\n```json\n{facts_str}\n```\n\n"
        f"## Agent Memory Feedback (last 5 runs)\n```json\n{memory_str}\n```\n\n"
        "Based on the above data, which actions from the catalog should be triggered? "
        "Respond ONLY with the JSON array."
    )

    if _GROQ_SDK_AVAILABLE and _GroqClient:
        client = _GroqClient(api_key=groq_api_key)
        response = client.chat.completions.create(
            model=groq_model,
            messages=[
                {"role": "system", "content": _build_llm_system_prompt()},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "[]"
    else:
        payload = {
            "model": groq_model,
            "messages": [
                {"role": "system", "content": _build_llm_system_prompt()},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        req = urllib_request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body["choices"][0]["message"]["content"] or "[]"

    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("actions", "action_ids", "decisions", "interventions"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        return next((v for v in parsed.values() if isinstance(v, list)), [])
    return []


def _validate_llm_actions(proposed_actions: list, memory_feedback: dict | None = None) -> tuple:
    validated = []
    hallucination_log = []
    seen: set = set()
    recurring_failures = (memory_feedback or {}).get("recurring_failed_action_ids", [])

    for item in proposed_actions:
        action_id = item if isinstance(item, str) else (item.get("action_id", "") if isinstance(item, dict) else "")
        action_id = str(action_id).strip()
        if not action_id:
            continue

        if action_id not in ACTION_CATALOG:
            hallucination_log.append({"rejected_action_id": action_id, "reason": "Not found in ACTION_CATALOG – hallucination blocked."})
            continue

        if action_id in seen:
            continue
        seen.add(action_id)

        action = _build_action_from_catalog(action_id)
        if action_id in recurring_failures:
            action["memory_note"] = "This action failed in multiple recent runs. Check credentials/connectivity before retry."
        validated.append(action)

    return validated, hallucination_log


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------

def _decide_actions_deterministic(facts: dict, memory_feedback: dict | None = None) -> list:
    actions = []
    memory_feedback = memory_feedback or {}

    if facts.get("build_failures", 0) > 0 or facts.get("last_build") in {"failure", "failed", "cancelled"}:
        actions.append(_build_action_from_catalog("slack-alert-build-failure-001"))
        actions.append(_build_action_from_catalog("ci-stabilize-001"))

    if facts.get("uat_state") in {"failure", "error", "inactive", "unknown", "not_found"}:
        actions.append(_build_action_from_catalog("slack-alert-uat-failure-001"))

    if facts.get("prod_state") in {"failure", "error", "inactive", "unknown", "not_found"}:
        actions.append(_build_action_from_catalog("slack-alert-prod-failure-001"))
        actions.append(_build_action_from_catalog("prod-readiness-001"))

    if facts.get("blocked_over_4d", 0) > 0:
        actions.append(_build_action_from_catalog("blocker-escalation-001"))
    elif facts.get("blocked_total", 0) > 0:
        actions.append(_build_action_from_catalog("blocker-triage-001"))

    if facts.get("pending_review_over_48h", 0) > 0:
        actions.append(_build_action_from_catalog("review-sla-001"))
    elif facts.get("pending_reviews", 0) > 0:
        actions.append(_build_action_from_catalog("review-window-001"))

    if facts.get("sprint_completion_pct", 0) < 50:
        actions.append(_build_action_from_catalog("scope-control-001"))

    if memory_feedback.get("skipped_due_to_webhook_last_5", 0) >= 2:
        actions.append(_build_action_from_catalog("execution-routing-001"))

    if not actions:
        actions.append(_build_action_from_catalog("monitor-only-001"))

    return actions


# ---------------------------------------------------------------------------
# Main decision function
# ---------------------------------------------------------------------------

def _decide_actions(
    facts: dict,
    memory_feedback: dict | None = None,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
) -> list:
    memory_feedback = memory_feedback or {}
    api_key = groq_api_key or os.getenv("GROQ_API_KEY", "").strip()
    model = groq_model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

    decision_metadata = {
        "decision_mode": "llm",
        "llm_model": model,
        "hallucination_log": [],
        "fallback_reason": None,
    }

    if not api_key:
        decision_metadata["decision_mode"] = "deterministic_fallback"
        decision_metadata["fallback_reason"] = "GROQ_API_KEY not set – using deterministic rules."
        actions = _decide_actions_deterministic(facts, memory_feedback)
        for a in actions:
            a["_decision_metadata"] = decision_metadata
        return actions

    try:
        proposed = _call_groq_for_decisions(facts, memory_feedback, api_key, model)
        validated, hallucination_log = _validate_llm_actions(proposed, memory_feedback)

        decision_metadata["hallucination_log"] = hallucination_log
        decision_metadata["llm_proposed_count"] = len(proposed)
        decision_metadata["validated_count"] = len(validated)

        if not validated:
            decision_metadata["decision_mode"] = "deterministic_fallback"
            decision_metadata["fallback_reason"] = "LLM returned no valid actions after hallucination filtering."
            actions = _decide_actions_deterministic(facts, memory_feedback)
        else:
            actions = validated

    except Exception as exc:
        decision_metadata["decision_mode"] = "deterministic_fallback"
        decision_metadata["fallback_reason"] = f"Groq API error: {exc}"
        actions = _decide_actions_deterministic(facts, memory_feedback)

    for a in actions:
        a["_decision_metadata"] = decision_metadata

    return actions


# ---------------------------------------------------------------------------
# Multi-cycle planner (public entry point)
# ---------------------------------------------------------------------------

def run_agentic_planner(
    final_report: dict,
    max_cycles: int = 2,
    memory_feedback: dict | None = None,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    intelligence: dict | None = None,
) -> dict:
    """
    Run up to *max_cycles* decision cycles, deduplicating action_ids across cycles.
    Intelligence-engine recommendations are merged in as the seed action set.
    """
    facts = _risk_facts(final_report)
    memory_feedback = memory_feedback or {}
    seen_action_ids: set = set()

    # Seed from intelligence engine recommendations
    intel_actions = []
    if intelligence and "recommendations" in intelligence:
        for rec in intelligence["recommendations"]:
            action_id = rec.get("action_id")
            if action_id and action_id in ACTION_CATALOG:
                intel_actions.append(_build_action_from_catalog(action_id))
                seen_action_ids.add(action_id)

    cycles = []
    for cycle in range(1, max_cycles + 1):
        proposed = _decide_actions(facts, memory_feedback=memory_feedback, groq_api_key=groq_api_key, groq_model=groq_model)
        new_actions = [a for a in proposed if a["action_id"] not in seen_action_ids]

        if not new_actions:
            cycles.append({"cycle": cycle, "observation": facts, "decisions": [], "status": "no_new_actions"})
            break

        for action in new_actions:
            seen_action_ids.add(action["action_id"])

        cycles.append({"cycle": cycle, "observation": facts, "decisions": new_actions, "status": "actions_generated"})

    action_queue = intel_actions + [a for cycle in cycles for a in cycle.get("decisions", [])]
    action_queue = _sort_actions_by_priority(action_queue)

    return {
        "enabled": True,
        "mode": "observe-think-decide-act-learn",
        "autonomy_level": "fully-autonomous",
        "goal": "Reduce delivery risk and improve sprint throughput",
        "observed_facts": facts,
        "memory_feedback": memory_feedback,
        "cycles_executed": len(cycles),
        "cycles": cycles,
        "action_queue": action_queue,
    }
