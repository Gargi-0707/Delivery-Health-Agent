# -*- coding: utf-8 -*-
"""
sprint_analyzer.py (Shim)
~~~~~~~~~~~~~~~~~~~~~~~~
Backward compatibility shim for the monolithic sprint_analyzer.py.
Delegates all work to the new modular package structure.
"""

import sys
import argparse
from reports.builder import generate_weekly_report
from core.utils import safe_print
from core.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_FALLBACK_MODEL,
    LATEST_FULL_REPORT_FILE
)
from core.logging import (
    METRICS,
    bootstrap_metrics_from_agent_memory,
    configure_structured_logging,
    log_event
)


def main():
    parser = argparse.ArgumentParser(description="AI Delivery Health Agent - Modular CLI")
    parser.add_argument("--ai", action="store_true", help="Include AI-generated insights via Groq")
    parser.add_argument("--agent", action="store_true", help="Run in agentic mode (Observe-Think-Decide-Act)")
    parser.add_argument("--execute", action="store_true", help="Enable autonomous action execution (DANGEROUS)")
    parser.add_argument("--json", action="store_true", help="Print the raw JSON report data to the terminal")
    args = parser.parse_args()

    try:
        result = generate_weekly_report(
            include_ai_insights=args.ai,
            agent_mode=args.agent,
            agent_execute=args.execute
        )

        if args.json:
            import json
            safe_print(json.dumps(result["report"], indent=2))

        safe_print("\n" + result["insights"])
        
    except Exception as e:
        safe_print(f"\nCRITICAL ERROR: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()