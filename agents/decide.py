# -*- coding: utf-8 -*-
"""
agents/decide.py
~~~~~~~~~~~~~~~~
DecideAgent — runs the agentic planner to select interventions.
"""

from engine.memory import _load_memory_state, _memory_feedback
from engine.planner import run_agentic_planner
from core.config import GROQ_API_KEY, GROQ_MODEL
from core.logging import log_event


class DecideAgent:
    """Decide phase: runs the multi-cycle planner and returns an action queue."""

    @staticmethod
    def run(analysis: dict) -> dict:
        """
        Args:
            analysis: dict returned by AnalyzerAgent.run()

        Returns:
            decisions dict with 'action_queue', 'observed_facts', etc.
        """
        report = analysis["report"]
        intelligence = report.get("intelligence")

        memory_state = _load_memory_state()
        mem_feedback = _memory_feedback(memory_state)

        decisions = run_agentic_planner(
            final_report=report,
            max_cycles=2,
            memory_feedback=mem_feedback,
            groq_api_key=GROQ_API_KEY,
            groq_model=GROQ_MODEL,
            intelligence=intelligence,
        )

        log_event(
            "info",
            "decide_complete",
            action_count=len(decisions.get("action_queue", [])),
            decision_mode=decisions.get("cycles", [{}])[0].get("decisions", [{}])[0].get("_decision_metadata", {}).get("decision_mode", "unknown") if decisions.get("cycles") else "unknown",
        )

        return decisions
