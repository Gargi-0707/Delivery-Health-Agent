# -*- coding: utf-8 -*-
"""
integrations/github_client.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
GitHub API client: authenticates, fetches PRs, workflow runs,
deployment states, and produces the CI/CD summary.
"""

from itertools import islice

from github import Auth, Github
from github.GithubException import BadCredentialsException

from core.config import GH_REPO, GH_TOKEN
from core.logging import METRICS, log_event


# ---------------------------------------------------------------------------
# Connection & Fetch
# ---------------------------------------------------------------------------

def connect_github():
    """Return (github_instance, repo) or raise RuntimeError on bad credentials."""
    gh = Github(auth=Auth.Token(GH_TOKEN))
    try:
        repo = gh.get_repo(GH_REPO)
        return gh, repo
    except BadCredentialsException:
        METRICS.record_external_api_failure("github")
        log_event("error", "github_auth_failed", error_type="BadCredentialsException")
        raise RuntimeError("❌ GitHub authentication failed")


def fetch_pull_requests(repo, max_prs: int = 50) -> list:
    """Return the most recent *max_prs* pull requests (open + closed)."""
    try:
        return list(islice(repo.get_pulls(state="all"), max_prs))
    except Exception as exc:
        METRICS.record_external_api_failure("github")
        log_event(
            "error", "github_pull_fetch_failed",
            error_type=type(exc).__name__, error_message=str(exc)
        )
        raise RuntimeError("❌ GitHub pull request fetch failed") from exc


# ---------------------------------------------------------------------------
# CI/CD Processing
# ---------------------------------------------------------------------------

def _latest_deployment_state(repo, environment_name: str) -> str:
    """Return the state of the most recent deployment in *environment_name*."""
    try:
        deployments = repo.get_deployments(environment=environment_name)
        latest = next(iter(deployments), None)
        if not latest:
            return "not_found"
        latest_status = next(iter(latest.get_statuses()), None)
        return latest_status.state.lower() if latest_status else "created"
    except Exception:
        return "unknown"


def process_cicd(repo) -> dict:
    """
    Analyse GitHub Actions workflow runs and deployment environments.

    Returns a dict with: build_failures, last_build, environments (uat/prod).
    """
    try:
        workflow_runs = list(islice(repo.get_workflow_runs(), 50))
    except Exception as exc:
        METRICS.record_external_api_failure("github")
        log_event(
            "warning", "github_cicd_fetch_failed",
            error_type=type(exc).__name__, error_message=str(exc)
        )
        workflow_runs = []

    build_failures = sum(
        1 for run in workflow_runs if (run.conclusion or "").lower() == "failure"
    )

    if workflow_runs:
        last_run = workflow_runs[0]
        last_build = (last_run.conclusion or last_run.status or "unknown").lower()
    else:
        last_build = "unknown"

    return {
        "build_failures": build_failures,
        "last_build": last_build,
        "environments": {
            "uat": _latest_deployment_state(repo, "uat"),
            "prod": _latest_deployment_state(repo, "prod"),
        },
    }
