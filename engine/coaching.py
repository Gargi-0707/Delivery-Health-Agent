# -*- coding: utf-8 -*-
"""
engine/coaching.py
~~~~~~~~~~~~~~~~~~
LLM-powered LearnAgent coaching: analyses the last N run records and
produces structured coaching insights (health score, bottlenecks, forecast).
"""

import json
import os
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import httpx
    from groq import Groq as _GroqClient, DefaultHttpxClient as _DefaultHttpxClient
    _GROQ_SDK_AVAILABLE = True
except ImportError:
    _GroqClient = None
    _DefaultHttpxClient = None
    _GROQ_SDK_AVAILABLE = False


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_coaching_system_prompt() -> str:
    return """You are an elite Delivery Coach AI. Your job is to analyze historical sprint data and provide long-term coaching.
You will be provided with a JSON array representing the last several runs of a delivery health agent.

Your goal is to identify recurring patterns, structural bottlenecks, and provide high-level strategic coaching.

## RESPONSE FORMAT
You MUST respond ONLY with a valid JSON object with the following schema:
{
  "recurring_bottlenecks": ["list of observed repeating issues"],
  "structural_recommendations": ["list of strategic, long-term changes needed"],
  "coaching_message": "a concise, encouraging, but firm paragraph for the team",
  "risk_forecast": "prediction of likely issues in the next 1-2 weeks",
  "health_score": 85,
  "health_trend": "improving" | "stable" | "degrading",
  "confidence": "high" | "medium" | "low"
}

Do NOT include any markdown, explanation, or extra text outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Groq call
# ---------------------------------------------------------------------------

def _call_groq_for_coaching(history_data: list, groq_api_key: str, groq_model: str) -> dict:
    history_str = json.dumps(history_data, indent=2)
    user_message = (
        f"## Historical Sprint Run Data (Last 10 runs)\n```json\n{history_str}\n```\n\n"
        "Analyze this data and provide coaching."
    )
    system_prompt = _build_coaching_system_prompt()

    if _GROQ_SDK_AVAILABLE and _GroqClient:
        http_client = _DefaultHttpxClient(proxy=None)
        client = _GroqClient(
            api_key=groq_api_key,
            http_client=http_client
        )
        response = client.chat.completions.create(
            model=groq_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
    else:
        payload = {
            "model": groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }
        req = urllib_request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        raw = body["choices"][0]["message"]["content"] or "{}"

    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _learn_with_llm(memory_state: dict, groq_api_key: str | None = None, groq_model: str | None = None) -> dict | None:
    """
    Analyse the last 5 runs using an LLM and return structured coaching insights.
    Returns None if no API key is set or the memory is empty.
    """
    api_key = groq_api_key or os.getenv("GROQ_API_KEY", "").strip()
    model = groq_model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

    if not api_key:
        return None

    runs = memory_state.get("runs", [])
    if not runs:
        return None

    history_sample = [
        {
            "timestamp_utc": r.get("timestamp_utc"),
            "observed_facts": r.get("observed_facts"),
            "action_ids": r.get("action_ids"),
            "status_counts": r.get("status_counts"),
            "trend": (r.get("trend") or {}).get("performance_summary"),
            "outcomes": [o.get("status") for o in (r.get("evaluated_previous_outcomes") or [])],
        }
        for r in runs[-5:]
    ]

    try:
        coaching = _call_groq_for_coaching(history_sample, api_key, model)
        coaching["_metadata"] = {"model": model, "runs_analyzed": len(history_sample)}
        return coaching
    except Exception:
        return None
