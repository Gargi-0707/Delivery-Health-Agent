# -*- coding: utf-8 -*-
"""
agents/learn.py
~~~~~~~~~~~~~~~
LearnAgent — evaluates past outcomes, generates auto-escalations,
runs trend analysis, and provides LLM-powered strategic coaching.
"""

from datetime import datetime, timezone
import os

from core.logging import log_event
from engine.memory import (
    _load_memory_state,
    _save_memory_state,
    _select_previous_executed_run,
    _select_trend_baseline_run,
    _trend_analysis,
    _memory_max_runs,
)
from engine.outcomes import _evaluate_action_outcomes, _derive_escalation_actions
from engine.coaching import _learn_with_llm


class LearnAgent:
    """Learn phase: evaluate outcomes and provide long-term coaching."""

    @staticmethod
    def run(
        decisions: dict,
        executions: dict,
        execute_enabled: bool = False,
        groq_api_key: str | None = None,
        groq_model: str | None = None,
    ) -> dict:
        """
        Evaluate past actions and persist the current run to memory.
        Returns a dict with 'learning', 'coaching', and 'escalations'.
        """
        observed_facts = decisions.get("observed_facts", {})
        action_queue = decisions.get("action_queue", [])

        # 1. Load memory and evaluate past outcomes
        memory_state = _load_memory_state()
        prev_executed_run = _select_previous_executed_run(memory_state)
        past_outcomes = _evaluate_action_outcomes(prev_executed_run, observed_facts)
        auto_escalations = _derive_escalation_actions(past_outcomes)

        # 2. Trend analysis
        baseline_run, basis = _select_trend_baseline_run(memory_state)
        trend = _trend_analysis(
            baseline_run.get("observed_facts") if baseline_run else None,
            observed_facts,
            comparison_basis=basis,
        )

        # 3. Persist current run to memory
        run_record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "run_id": executions.get("run_id"),
            "observed_facts": observed_facts,
            "action_ids": [a["action_id"] for a in action_queue],
            "executed_actions": executions.get("executed_actions", []),
            "status_counts": executions.get("status_counts", {}),
            "trend": trend,
            "evaluated_previous_outcomes": past_outcomes,
        }

        memory_state["runs"].append(run_record)
        max_runs = _memory_max_runs()
        if len(memory_state["runs"]) > max_runs:
            memory_state["runs"] = memory_state["runs"][-max_runs:]

        _save_memory_state(memory_state)

        # 4. LLM Coaching
        ai_coaching = _learn_with_llm(
            memory_state, groq_api_key=groq_api_key, groq_model=groq_model
        )

        log_event(
            "info",
            "learn_complete",
            auto_escalations=len(auto_escalations),
            has_ai_coaching=ai_coaching is not None,
        )

        return {
            "evaluated_past_outcomes": past_outcomes,
            "auto_escalations": auto_escalations,
            "trend": trend,
            "ai_coaching": ai_coaching,
        }
