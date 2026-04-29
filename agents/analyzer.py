# -*- coding: utf-8 -*-
"""
agents/analyzer.py
~~~~~~~~~~~~~~~~~~
AnalyzerAgent — processes raw observations into the structured report
and optionally enriches it with Groq LLM insights.
"""

import time
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request
import json

try:
    from groq import Groq as _GroqClient
    _GROQ_SDK_AVAILABLE = True
except ImportError:
    _GroqClient = None
    _GROQ_SDK_AVAILABLE = False

from core.config import (
    GROQ_API_KEY,
    GROQ_FALLBACK_MODEL,
    GROQ_MODEL,
)
from core.logging import METRICS, log_event
from core.utils import safe_print
from integrations.jira_client import process_sprint
from reports.signals import build_health_signals, build_recommendations
from reports.charts import build_report_charts


# ---------------------------------------------------------------------------
# LLM Insights
# ---------------------------------------------------------------------------

_INSIGHTS_SYSTEM_PROMPT = """You are a senior engineering delivery consultant.
Analyze the following sprint health report and provide:
1. A concise executive summary (2-3 sentences)
2. Top 3 risk areas with specific, actionable mitigation steps
3. One strategic recommendation for the next sprint
Format your response as plain text with clear sections."""


def get_groq_insights(report: dict, api_key: str | None = None, model: str | None = None) -> str | None:
    """
    Call Groq to generate natural-language insights from the aggregated report.
    Falls back to the GROQ_FALLBACK_MODEL on rate-limit errors.
    Returns None if no API key is configured.
    """
    api_key = api_key or GROQ_API_KEY
    model = model or GROQ_MODEL

    if not api_key:
        return None

    # Build a trimmed context (strips heavy issue lists to save tokens)
    trimmed = {k: v for k, v in report.items() if k not in {"intelligence"}}
    if "jira" in trimmed:
        trimmed["jira"] = {k: v for k, v in trimmed["jira"].items() if k != "issues"}
    if "github" in trimmed:
        trimmed["github"] = {k: v for k, v in trimmed["github"].items() if k != "prs"}

    report_str = json.dumps(trimmed, indent=2)[:6000]  # token guard

    for attempt_model in [model, GROQ_FALLBACK_MODEL]:
        try:
            if _GROQ_SDK_AVAILABLE and _GroqClient:
                client = _GroqClient(api_key=api_key)
                response = client.chat.completions.create(
                    model=attempt_model,
                    messages=[
                        {"role": "system", "content": _INSIGHTS_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Sprint Health Report:\n\n{report_str}"},
                    ],
                    temperature=0.3,
                    max_tokens=1024,
                )
                return response.choices[0].message.content
            else:
                payload = {
                    "model": attempt_model,
                    "messages": [
                        {"role": "system", "content": _INSIGHTS_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Sprint Health Report:\n\n{report_str}"},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1024,
                }
                req = urllib_request.Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib_request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]

        except urllib_error.HTTPError as exc:
            if exc.code == 429:
                log_event("warning", "groq_rate_limit", model=attempt_model)
                time.sleep(2)
                continue
            log_event("error", "groq_http_error", model=attempt_model, code=exc.code)
            return None
        except Exception as exc:
            log_event("error", "groq_insights_failed", model=attempt_model, error=str(exc))
            if attempt_model == GROQ_FALLBACK_MODEL:
                return None

    return None


# ---------------------------------------------------------------------------
# AnalyzerAgent
# ---------------------------------------------------------------------------

class AnalyzerAgent:
    """Analyze phase: turns raw observations into the structured health report."""

    @staticmethod
    def run(observations: dict, include_ai_insights: bool = True, runtime: dict | None = None) -> dict:
        """
        Returns:
            {
                "report": {...},
                "insights": "str | None",
            }
        """
        issues = observations["issues"]
        pulls = observations["pulls"]
        cicd = observations["cicd"]
        slack = observations["slack"]

        (
            completion_pct,
            overall_completion_pct,
            sprint_summary,
            jira_summary,
            github_summary,
        ) = process_sprint(issues, pulls)

        signals = build_health_signals(jira_summary, github_summary, cicd)
        recommendations = build_recommendations(jira_summary, github_summary, cicd)
        charts = build_report_charts(jira_summary)

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sprint_completion_pct": completion_pct,
            "overall_project_progress_pct": overall_completion_pct,
            "jira": jira_summary,
            "github": github_summary,
            "cicd": cicd,
            "slack": slack,
            "signals": signals,
            "recommendations": recommendations,
            "charts": charts,
            "runtime": runtime or {},
        }

        insights = None
        if include_ai_insights:
            insights = get_groq_insights(report)

        METRICS.record_run_success({"report": report})
        log_event("info", "analyze_complete", completion_pct=completion_pct)

        return {"report": report, "insights": insights}
