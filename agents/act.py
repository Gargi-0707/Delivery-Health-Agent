# -*- coding: utf-8 -*-
"""
agents/act.py
~~~~~~~~~~~~~
ActAgent — executes the action queue via Slack webhooks and Jira tickets.
"""

import json
import os
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from groq import Groq as _GroqClient
    _GROQ_SDK_AVAILABLE = True
except ImportError:
    _GroqClient = None
    _GROQ_SDK_AVAILABLE = False

from core.logging import log_event


# ---------------------------------------------------------------------------
# Internal helpers (preserved from agentic_engine.py)
# ---------------------------------------------------------------------------

def _post_webhook_alert(webhook_url: str, payload: dict) -> int:
    req = urllib_request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=15) as response:
        return response.getcode()


def _create_jira_escalation_ticket(action: dict, run_id: str) -> dict:
    import base64
    jira_server = os.getenv("JIRA_SERVER", "").strip().rstrip("/")
    jira_email = os.getenv("JIRA_EMAIL", "").strip()
    jira_token = os.getenv("JIRA_TOKEN", "").strip()
    jira_project = os.getenv("JIRA_ESCALATION_PROJECT", "SHOP").strip() or "SHOP"

    if not all([jira_server, jira_email, jira_token]):
        return {"ok": False, "status": "skipped", "detail": "Jira escalation skipped: missing JIRA_SERVER/JIRA_EMAIL/JIRA_TOKEN."}

    original_action_id = action.get("original_action_id", "unknown-action")
    evidence = action.get("outcome_evidence", "No evidence")
    hours_since_action = action.get("hours_since_action", "unknown")

    summary = action.get("jira_draft_summary", f"[AUTO-ESCALATION] {original_action_id} unresolved after 24h")
    description = action.get("jira_draft_description") or (
        f"Run: {run_id}\nOriginal action: {original_action_id}\nAge hours: {hours_since_action}\nEvidence: {evidence}\nEscalation objective: {action.get('objective', 'N/A')}"
    )

    payload = {
        "fields": {
            "project": {"key": jira_project},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Task"},
            "labels": ["delivery-health-agent", "auto-escalation"],
        }
    }

    url = f"{jira_server}/rest/api/2/issue"
    token = base64.b64encode(f"{jira_email}:{jira_token}".encode("utf-8")).decode("ascii")
    req = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return {"ok": True, "status": "executed", "detail": f"Jira escalation ticket created: {body.get('key', 'unknown')}"}
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "failed", "detail": f"Jira escalation failed: {exc}"}


def _notify_senior_stakeholders(action: dict, run_id: str, default_webhook_url: str) -> dict:
    stakeholder_webhook = os.getenv("AGENT_STAKEHOLDER_WEBHOOK_URL", "").strip() or default_webhook_url
    if not stakeholder_webhook:
        return {"ok": False, "status": "skipped", "detail": "Stakeholder notification skipped: no stakeholder webhook configured."}

    payload = {
        "text": (
            "[Delivery Health Agent][P0 Escalation] "
            f"{action.get('objective', 'Escalation triggered')} | "
            f"Original action: {action.get('original_action_id', 'unknown')} | "
            f"Evidence: {action.get('outcome_evidence', 'No evidence')}"
        ),
        "run_id": run_id,
        "action_id": action.get("action_id"),
        "original_action_id": action.get("original_action_id"),
        "priority": action.get("priority", "P0"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    try:
        status_code = _post_webhook_alert(stakeholder_webhook, payload)
        return {"ok": True, "status": "executed", "detail": f"Stakeholder notification sent (HTTP {status_code})."}
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError) as exc:
        return {"ok": False, "status": "failed", "detail": f"Stakeholder notification failed: {exc}"}


def _build_action_draft_system_prompt() -> str:
    return """You are a highly professional and polite Engineering Delivery Agent.
Your task is to write context-aware messages for delivery interventions.

## OBJECTIVE
Write a specific, actionable, and polite message for a stakeholder (Scrum Master, Engineering Manager, etc.)
regarding a delivery risk. Use the provided sprint facts to add weight and urgency to the message.

## GUIDELINES
- Be professional but empathetic.
- Mention specific numbers or issues (e.g., "SHOP-9 is blocked", "completion is only 25%").
- Clearly state what action is needed.
- Keep it concise (max 3 sentences).
- If it's an escalation, be firm but professional.

## RESPONSE FORMAT
You MUST respond ONLY with a valid JSON object with the following schema:
{
  "slack_message": "The personalized message for Slack/Teams",
  "jira_summary": "Short summary for a Jira ticket (if needed)",
  "jira_description": "Detailed description for a Jira ticket (if needed)"
}

Do NOT include any markdown, explanation, or extra text outside the JSON object.
"""


def _call_groq_for_action_draft(action: dict, facts: dict, groq_api_key: str, groq_model: str) -> dict:
    action_str = json.dumps(action, indent=2)
    facts_str = json.dumps(facts, indent=2)
    user_message = (
        f"## Proposed Action\n```json\n{action_str}\n```\n\n"
        f"## Current Sprint Facts\n```json\n{facts_str}\n```\n\n"
        "Draft a personalized intervention message for this action."
    )
    system_prompt = _build_action_draft_system_prompt()

    if _GROQ_SDK_AVAILABLE and _GroqClient:
        client = _GroqClient(api_key=groq_api_key)
        response = client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}],
            temperature=0.7,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
    else:
        payload = {"model": groq_model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], "temperature": 0.7, "max_tokens": 512, "response_format": {"type": "json_object"}}
        req = urllib_request.Request("https://api.groq.com/openai/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body["choices"][0]["message"]["content"] or "{}"

    return json.loads(raw)


def execute_actions(
    actions: list,
    run_id: str | None = None,
    execute_enabled: bool = False,
    facts: dict | None = None,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
) -> list:
    """Execute the action queue — sends webhooks and creates Jira tickets."""
    executed = []
    webhook_url = os.getenv("AGENT_ALERT_WEBHOOK_URL", "").strip()
    api_key = groq_api_key or os.getenv("GROQ_API_KEY", "").strip()
    model = groq_model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

    for action in actions:
        # AI action drafting
        if api_key and facts:
            try:
                draft = _call_groq_for_action_draft(action, facts, api_key, model)
                if draft.get("slack_message"):
                    action["execute_message"] = draft["slack_message"]
                if draft.get("jira_summary"):
                    action["jira_draft_summary"] = draft["jira_summary"]
                if draft.get("jira_description"):
                    action["jira_draft_description"] = draft["jira_description"]
            except Exception:
                pass

        action_id = action.get("action_id")
        text = action.get("execute_message") or action.get("objective") or "No message"
        payload = {
            "text": f"[Delivery Health Agent] {text}",
            "action_id": action_id,
            "alert_type": action.get("alert_type", "general"),
            "priority": action.get("priority", "P3"),
            "owner": action.get("owner", "Unassigned"),
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

        # Special: strategic coaching report
        if action_id == "strategic-coaching-report":
            if not execute_enabled:
                executed.append({"action_id": action_id, "status": "dry_run", "detail": "Coaching report planned."})
                continue
            coaching = (facts.get("ai_coaching") if facts else None)
            if not coaching:
                executed.append({"action_id": action_id, "status": "skipped", "detail": "No coaching data available."})
                continue

            score = coaching.get("health_score", 0)
            trend = coaching.get("health_trend", "stable")
            msg = coaching.get("coaching_message", "")
            bottlenecks = "\\n".join([f"• {b}" for b in coaching.get("recurring_bottlenecks", [])])
            recs = "\\n".join([f"• {r}" for r in coaching.get("structural_recommendations", [])])
            forecast = coaching.get("risk_forecast", "N/A")
            report_text = (
                "💡 *AI STRATEGIC COACHING REPORT*\n"
                f"*Health Score:* {score}/100 ({trend})\n\n"
                f"*Insight:* \"{msg}\"\n\n"
                f"*Recurring Bottlenecks:*\n{bottlenecks}\n\n"
                f"*Recommendations:*\n{recs}\n\n"
                f"*Risk Forecast (2w):*\n{forecast}"
            )
            payload["text"] = report_text.replace("•", "*")
            if not execute_enabled:
                executed.append({"action_id": action_id, "status": "dry_run", "detail": "Execution disabled. Escalation planned only."})
                continue

            jira_result = _create_jira_escalation_ticket(action, run_id)
            notify_result = _notify_senior_stakeholders(action, run_id, webhook_url)
            parts = [jira_result.get("detail"), notify_result.get("detail")]
            success_count = sum(1 for r in [jira_result, notify_result] if r.get("ok"))
            skipped_count = sum(1 for r in [jira_result, notify_result] if r.get("status") == "skipped")
            status = "executed" if success_count > 0 else "skipped" if skipped_count == 2 else "failed"
            executed.append({"action_id": action_id, "status": status, "detail": " | ".join(p for p in parts if p)})
            continue

        if not execute_enabled:
            executed.append({"action_id": action_id, "status": "dry_run", "detail": "Execution disabled. Planned action only."})
            continue

        if not webhook_url:
            executed.append({"action_id": action_id, "status": "skipped", "detail": "AGENT_ALERT_WEBHOOK_URL not configured."})
            continue

        try:
            status_code = _post_webhook_alert(webhook_url, payload)
            executed.append({"action_id": action_id, "status": "executed", "detail": f"Alert sent (HTTP {status_code})."})
        except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError) as exc:
            executed.append({"action_id": action_id, "status": "failed", "detail": str(exc)})

    return executed


# ---------------------------------------------------------------------------
# ActAgent class
# ---------------------------------------------------------------------------

class ActAgent:
    """Act phase: execute the action queue."""

    @staticmethod
    def run(
        decisions: dict,
        execute_enabled: bool = False,
        groq_api_key: str | None = None,
        groq_model: str | None = None,
    ) -> dict:
        import uuid
        run_id = str(uuid.uuid4())[:8]
        action_queue = decisions.get("action_queue", [])
        facts = decisions.get("observed_facts", {})

        executed = execute_actions(
            action_queue,
            run_id=run_id,
            execute_enabled=execute_enabled,
            facts=facts,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
        )

        status_counts = {}
        for item in executed:
            s = item.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        log_event("info", "act_complete", executed_count=len(executed), status_counts=status_counts)

        return {
            "run_id": run_id,
            "executed_actions": executed,
            "status_counts": status_counts,
        }
