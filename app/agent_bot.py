import json
import os
import time
import traceback
import re
from groq import Groq
from sprint_analyzer import GROQ_API_KEY, GROQ_MODEL, GROQ_FALLBACK_MODEL, LATEST_FULL_REPORT_FILE
from agentic_engine import _load_memory_state, _memory_file_path

SYSTEM_PROMPT_TEMPLATE = """
You are the AI Delivery Intelligence Assistant. You have full visibility into Jira, GitHub, Slack, and project forecasts.

## YOUR KNOWLEDGE BASE
{context_json}

## YOUR CAPABILITIES & INSTRUCTIONS
1. **Jira (Advanced)**: You have a high-density index of issues.
   - Column Mapping: [ID, Status, Assignee, Points, Type, Sprint, Title]
2. **GitHub (Advanced)**: You have a high-density index of Pull Requests.
   - Column Mapping: [ID, Status, Author, Merged, Iterations, Title, Comments]
   - Use `summary.github.total_comments` for the total number of comments.
3. **Slack**: Use `slack_highlights` for latest discussions and team alerts.
4. **Forecasting**: For "how many sprints to finish" questions, ALWAYS use `intelligence.forecast` block — NEVER calculate manually.
5. **Memory**: Use conversation history to resolve pronouns like "he", "she", "it", or "those".
6. **Next Sprint Prediction**: When asked about the NEXT sprint, ALWAYS use the `next_sprint_prediction` block from the context.
   - `next_sprint_prediction.next_sprint_name`: predicted sprint name following the pattern "Sprint N - MVP N+1"
     (e.g. the sprints in data are Sprint 1-MVP 2, Sprint 2-MVP 3, … Sprint 5-MVP 6, so the next is "Sprint 6 - MVP 7").
   - `next_sprint_prediction.sprint_weeks`: sprint duration in weeks (default: 1).
   - `next_sprint_prediction.recommended_capacity_sp`: recommended story point capacity for the sprint.
   - `next_sprint_prediction.allocated_sp`: total story points of selected issues.
   - `next_sprint_prediction.issue_count`: number of recommended issues.
   - `next_sprint_prediction.issues`: list of recommended issues. Each issue has: id, title, type, points, status, assignee, complexity (Small/Medium/Large).
   - `next_sprint_prediction.type_breakdown`: count of each issue type (Bug, Story, Task, etc.).
   - `next_sprint_prediction.velocity_trend.per_sprint`: list of past sprints each with sp_assigned (planned), sp_done (completed), completion_pct.
   - `next_sprint_prediction.velocity_trend.avg_sp_assigned`: average SP assigned per sprint across history.
   - `next_sprint_prediction.velocity_trend.avg_sp_done`: average SP actually completed per sprint.
   - `next_sprint_prediction.note`: full reasoning explanation including naming pattern, capacity logic, and trend analysis.
   - When presenting: show sprint name, weeks, capacity, velocity trend, issue table (ID | Title | Type | Complexity | Points), type breakdown, and the full reasoning note.
   - ALWAYS explain WHY each issue was selected (priority: Bugs first → Stories → Tasks; balanced complexity mix).

## CRITICAL RULES
- **Global Stats**: ALWAYS use the `summary` block for total counts (e.g., total To Do: 27). NEVER manually count from the indexes.
- **Status Matching**: Be precise with statuses (e.g., "Dev in progress" vs "In Progress").
- **No Hallucinations**: Only report data that exists in the context.
- **Next Sprint Questions**: ALWAYS use `next_sprint_prediction` - do NOT make up sprint content.
- **Sprint Forecast / Sprints to Complete**: ALWAYS read from `intelligence.forecast`:
  - `intelligence.forecast.p50_sprints` = realistic estimate (50th percentile Monte Carlo)
  - `intelligence.forecast.p85_sprints` = safe/conservative estimate (85th percentile)
  - `intelligence.adjusted_velocity.adjusted_velocity` = velocity used (based on avg SP ASSIGNED per sprint, risk-adjusted)
  - `intelligence.backlog.remaining_story_points` = total remaining scope
  - NEVER divide `remaining_story_points / avg_sp_done` — `avg_sp_done` is a historical observation, NOT the planning velocity.
  - The correct formula already computed is: remaining_sp / adjusted_velocity (assigned-based), giving p50_sprints.
  - Always show both P50 (realistic) and P85 (safe) estimates.
7. **Team Capacity**: Use `intelligence.team_capacity` for per-person workload questions.
   - `members[].assignee`, `total_sp`, `completed_sp`, `in_progress_sp`, `blocked_issues`, `completion_rate_pct`, `load_status` (Overcommitted / Balanced / Under-utilised).
   - Show as a table sorted by total_sp descending. Flag overcommitted members.
8. **Sprint Retrospectives**: Use `intelligence.sprint_retrospectives` for per-sprint health analysis.
   - `sprints[].sprint`, `sp_completion_rate_pct`, `health` (Healthy / At Risk / Critical), `highlights.completed`, `highlights.still_open`, `highlights.blocked`.
   - `avg_sp_completion_rate_pct` and `summary` give the overall delivery health verdict.
9. **PR Code Quality**: Use `intelligence.pr_quality` for code review and rework analysis.
   - `avg_review_iterations`, `rework_rate_pct`, `quality_score` (0-100), `quality_label`, `high_rework_prs`, `pending_over_48h`.
   - A quality_score >= 80 is High, 50-79 is Medium, <50 is Low.
10. **Stale / At-Risk Issues**: Use `intelligence.stale_issues` for carry-over risk.
    - `total_stale_issues`, `total_stale_sp`, `critical_count` (blocked), `high_risk_count` (not started in sprint), `issues[]` with risk_level (Critical / High / Medium).
    - Always surface the `insight` field as a one-line executive summary.
11. **Delivery Risk Score**: Use `intelligence.delivery_risk_score` for the single daily KPI.
    - `score` (0-100), `label` (Healthy/Moderate Risk/High Risk/Critical), `color`, `breakdown[]` with factor+deduction+detail.
    - Always show score, label, and the top 3 breakdown factors.
    - `interpretation` gives a pre-formatted one-liner.
12. **Team Health Score**: Use `intelligence.team_health_score` for the composite team KPI.
    - `score` (0-100), `grade` (Excellent/Good/Needs Attention/Critical), `components` with sp_delivery/code_quality/issue_hygiene/capacity_balance each having score and max.
    - `interpretation` gives the pre-formatted breakdown string.
13. **Executive Summary**: When asked for a weekly summary, executive report, or boardroom update, return `intelligence.executive_summary.text` VERBATIM — it is a pre-formatted markdown report.
    - Also available: `delivery_risk_score`, `team_health_score`, `forecast_p50_sprints`, `forecast_p85_sprints`, `next_sprint_name`.
14. **Sprint-over-Sprint Trend**: Use `intelligence.sprint_over_sprint_trend` for improvement questions.
    - `trajectory` (IMPROVING / RECOVERING / STABLE / DECLINING), `verdict` (plain-language summary).
    - `sprints[]` each has: `sprint`, `sp_assigned`, `sp_done`, `sp_completion_rate`, `sp_rate_delta` (vs previous sprint), `direction` (improving/stable/declining/baseline).
    - `best_sprint` and `worst_sprint` have name + sp_completion_rate.
    - Always show the trend table and the verdict when asked about team improvement.
15. **Current Active Work Snapshot**: Use `intelligence.active_work_snapshot` for standup / "who is working on what" questions.
    - `total_active_issues`, `total_active_sp`, `assignee_count`, `snapshot_note`.
    - `members[]` each has: `assignee`, `active_issues`, `active_sp`, `blocked_count`, `issues[]`.
    - Each issue has: `id`, `title`, `status`, `sprint`, `points`, `blocked`, `has_pr`, `type`.
    - Active = status is NOT Done/Closed/Resolved AND NOT To Do/Open (work has started but not finished).
    - Show as a table grouped by assignee. Flag blocked issues with a warning.

Generated At: {generated_at}

User Question:
{user_question}
"""

def get_latest_context(user_question=""):
    """
    Unified context builder with High-Density Jira and GitHub indexes.
    """
    context = {
        "summary": {
            "jira": {},
            "github": {},
            "velocity": {},
            "scope": {}
        },
        "issue_header": ["ID", "Status", "Assignee", "Points", "Type", "Sprint", "Title"],
        "issue_index": [],
        "pr_header": ["ID", "Status", "Author", "Merged", "Iterations", "Title", "Comments"],
        "pr_index": [],
        "slack_highlights": [],
        "generated_at": "Unknown"
    }

    try:
        if os.path.exists(LATEST_FULL_REPORT_FILE):
            with open(LATEST_FULL_REPORT_FILE, "r", encoding="utf-8") as f:
                full_report = json.load(f)
                
                jira = full_report.get("jira", {})
                github = full_report.get("github", {})
                slack = full_report.get("slack", {})
                
                # 1. JIRA SUMMARY
                context["summary"]["jira"] = {
                    "total": jira.get("total_tasks"),
                    "status_counts": jira.get("status_counts", {}),
                    "canonical": jira.get("canonical_status_counts", {}),
                    "progress": jira.get("sprint_progress_pct")
                }
                
                sprint_metrics = jira.get("sprint_metrics", {})
                velocity = {}
                total_rem = 0
                backlog_pts = 0
                for s_name, m in sprint_metrics.items():
                    if s_name == "Backlog":
                        backlog_pts = m.get("points_total", 0) - m.get("points_done", 0)
                    else:
                        velocity[s_name] = m.get("points_done", 0)
                        total_rem += (m.get("points_total", 0) - m.get("points_done", 0))
                
                context["summary"]["velocity"] = velocity
                context["summary"]["scope"] = {"remaining_pts": total_rem + backlog_pts}

                # 2. GITHUB SUMMARY
                context["summary"]["github"] = {
                    "total_prs": github.get("total_prs"),
                    "total_comments": github.get("review_comments"),
                    "pending": github.get("pending_reviews")
                }

                # 3. SLACK
                slack_msgs = slack.get("issue_highlights", []) + slack.get("success_highlights", [])
                context["slack_highlights"] = list(dict.fromkeys(slack_msgs))[-5:]

                # 4. SMART PRIORITY JIRA INDEX
                jira_issues = jira.get("issues", {})
                all_issues = []
                for s_name, issues in jira_issues.items():
                    for issue in issues:
                        all_issues.append({
                            "data": [
                                issue.get("id"),
                                issue.get("status"),
                                issue.get("assignee") or "Unassigned",
                                issue.get("points") or 0,
                                issue.get("type") or "Task",
                                s_name,
                                issue.get("title")
                            ],
                            "is_blocked": issue.get("blocked", False),
                            "priority_rank": 0 if issue.get("priority") == "High" else 1
                        })
                
                q_lower = user_question.lower()
                target_id = None
                if "shop-" in q_lower:
                    match = re.search(r'shop-\d+', q_lower)
                    if match:
                        target_id = match.group(0).upper()

                # Priority Sorting: 1. Target ID, 2. Blocked, 3. High Priority
                def issue_ranker(item):
                    rank = 10
                    if target_id and item["data"][0] == target_id: rank = 0
                    elif item["is_blocked"]: rank = 1
                    elif item["priority_rank"] == 0: rank = 2
                    return rank

                all_issues.sort(key=issue_ranker)
                
                # Dynamic Slicing: take top 100 high-value issues
                context["issue_index"] = [item["data"] for item in all_issues[:100]]
                context["context_metadata"] = {
                    "total_issues_available": len(all_issues),
                    "issues_in_context": len(context["issue_index"]),
                    "was_trimmed": len(all_issues) > 100
                }

                # 5. SMART PRIORITY GITHUB INDEX
                prs = github.get("prs", [])
                all_prs = []
                for pr in prs:
                    pr_id = pr.get("shop_id") or pr.get("id")
                    all_prs.append({
                        "data": [
                            pr_id,
                            pr.get("status"),
                            pr.get("author") or "Unknown",
                            pr.get("merged", False),
                            pr.get("iterations", 0),
                            pr.get("branch") or pr.get("title"),
                            pr.get("comments_text", [])[:1]
                        ],
                        "is_pending": "pending" in str(pr.get("status")).lower()
                    })
                
                # Priority Sorting: 1. Target ID, 2. Pending Review
                def pr_ranker(item):
                    rank = 5
                    if target_id and str(item["data"][0]) == target_id: rank = 0
                    elif item["is_pending"]: rank = 1
                    return rank

                all_prs.sort(key=pr_ranker)
                context["pr_index"] = [item["data"] for item in all_prs[:20]]

                # 6. INTELLIGENCE BLOCK — forecast, velocity, backlog, next sprint
                intelligence = full_report.get("intelligence", {})
                next_sprint_prediction = intelligence.get("next_sprint_prediction")
                if next_sprint_prediction:
                    context["next_sprint_prediction"] = next_sprint_prediction
                # Inject forecast and velocity for "how many sprints" questions
                if intelligence.get("forecast"):
                    context["intelligence"] = {
                        "forecast": intelligence.get("forecast", {}),
                        "adjusted_velocity": intelligence.get("adjusted_velocity", {}),
                        "backlog": {
                            "remaining_story_points": intelligence.get("backlog", {}).get("remaining_story_points"),
                            "weighted_remaining_work": intelligence.get("backlog", {}).get("weighted_remaining_work"),
                        },
                        "velocity": intelligence.get("velocity", {}),
                        "team_capacity": intelligence.get("team_capacity", {}),
                        "sprint_retrospectives": intelligence.get("sprint_retrospectives", {}),
                        "pr_quality": intelligence.get("pr_quality", {}),
                        "stale_issues": intelligence.get("stale_issues", {}),
                        "delivery_risk_score": intelligence.get("delivery_risk_score", {}),
                        "team_health_score": intelligence.get("team_health_score", {}),
                        "executive_summary": intelligence.get("executive_summary", {}),
                        "sprint_over_sprint_trend": intelligence.get("sprint_over_sprint_trend", {}),
                        "active_work_snapshot": intelligence.get("active_work_snapshot", {}),
                    }

        memory_path = _memory_file_path()
        if os.path.exists(memory_path):
            with open(memory_path, "r", encoding="utf-8") as f:
                memory = json.load(f)
                if memory.get("runs"):
                    latest_run = memory["runs"][-1]
                    context["generated_at"] = latest_run.get("timestamp_utc", "Unknown")
    except Exception as e:
        print(f"Error building unified context: {e}")

    return context

def ask_delivery_bot(question: str, history=None):
    if history is None: history = []
    context = get_latest_context(question)
    api_key = GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if not api_key: return "❌ Error: No Groq API Key configured."

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        user_question=question,
        context_json=json.dumps(context, separators=(',', ':')),
        generated_at=context["generated_at"]
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-4:])
    messages.append({"role": "user", "content": question})

    client = Groq(api_key=api_key)

    # Model fallback chain: primary → fast → ultra-light
    models = [
        GROQ_MODEL or "llama-3.3-70b-versatile",
        GROQ_FALLBACK_MODEL or "llama-3.1-8b-instant",
        "gemma2-9b-it",
    ]

    last_error = ""
    for attempt, model in enumerate(models):
        # Exponential backoff before each retry (0s, 2s, 4s)
        if attempt > 0:
            wait_secs = 2 ** attempt
            time.sleep(wait_secs)

        try:
            # Trim context if payload is large to avoid token limit errors
            prompt_text = system_prompt
            if len(prompt_text) > 28000:
                # Remove large indexes to shrink payload
                slim_ctx = {k: v for k, v in context.items()
                            if k not in ("issue_index", "pr_index")}
                prompt_text = SYSTEM_PROMPT_TEMPLATE.format(
                    user_question=question,
                    context_json=json.dumps(slim_ctx, separators=(',', ':')),
                    generated_at=context["generated_at"]
                )
                messages[0] = {"role": "system", "content": prompt_text}

            chat_completion = client.chat.completions.create(
                messages=messages,
                model=model,
                max_tokens=1024,
            )
            return chat_completion.choices[0].message.content

        except Exception as e:
            err = str(e).lower()
            last_error = str(e)
            if "rate_limit" in err or "429" in err or "413" in err or "too large" in err:
                # Rate limited — try next model after backoff
                continue
            # Non-rate-limit error — return immediately
            return f"❌ AI Engine Error ({model}): {str(e)}"

    return (
        "⏳ The AI engine is currently rate-limited across all models. "
        "Please wait **30-60 seconds** and try again. "
        f"(Last error: {last_error[:120]})"
    )

