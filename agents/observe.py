# -*- coding: utf-8 -*-
"""
agents/observe.py
~~~~~~~~~~~~~~~~~
ObserveAgent — fetches data from all external sources (Jira, GitHub, Slack)
and returns a structured observations dict.
"""

from core.logging import METRICS, log_event
from integrations.jira_client import connect_jira, fetch_jira_issues, process_sprint
from integrations.github_client import connect_github, fetch_pull_requests, process_cicd
from integrations.slack_client import (
    fetch_slack_messages,
    analyze_slack_messages,
    build_compact_slack_summary,
    print_slack_config_warning,
    print_slack_fetch_diagnostics,
)


class ObserveAgent:
    """Observe phase: collect raw data from all integrations."""

    @staticmethod
    def run() -> dict:
        """
        Returns:
            {
                "issues": [...],
                "pulls": [...],
                "repo": <repo object>,
                "cicd": {...},
                "slack": {...},
            }
        """
        # ── Jira ──────────────────────────────────────────────────────────────
        jira_client = connect_jira()
        issues = fetch_jira_issues(jira_client)
        log_event("info", "observe_jira_complete", issue_count=len(issues))

        # ── GitHub ────────────────────────────────────────────────────────────
        _gh, repo = connect_github()
        pulls = fetch_pull_requests(repo, max_prs=50)
        cicd = process_cicd(repo)
        log_event("info", "observe_github_complete", pr_count=len(pulls), build_failures=cicd.get("build_failures", 0))

        # ── Slack ─────────────────────────────────────────────────────────────
        print_slack_config_warning()
        messages, fetch_diagnostics = fetch_slack_messages()
        slack_full = analyze_slack_messages(messages, fetch_diagnostics)
        print_slack_fetch_diagnostics(slack_full)
        slack_compact = build_compact_slack_summary(slack_full)
        log_event("info", "observe_slack_complete", message_count=slack_full.get("message_count", 0))

        return {
            "issues": issues,
            "pulls": pulls,
            "repo": repo,
            "cicd": cicd,
            "slack": slack_compact,
        }
