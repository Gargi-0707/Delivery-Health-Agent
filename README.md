# Sprint MVP Analyzer

This project combines Jira, GitHub, CI/CD, and optional Slack conversation analysis to produce a weekly delivery health report.

## What Slack Is Used For

Slack data is report-only.
It does not affect sprint completion, story-point math, or delivery scoring.
It is only used to generate a better weekly narrative about:

- issues and incidents
- delivery blockers
- successful releases or fixes
- review and dependency bottlenecks

## Slack Integration

Use Slack API access to read live Slack channels.

Use this when you want the analyzer to read live Slack channels.

Required environment variables:

```env
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_CHANNEL_IDS=C01234567,C02345678
SLACK_LOOKBACK_DAYS=7
SLACK_MESSAGE_LIMIT=250
```

How it works:

- The script calls Slack conversations.history for the listed channels.
- It reads only channels the bot can access.
- The bot must be installed to the workspace and invited to the target channels.

Recommended Slack app scopes:

- channels:history for public channels
- groups:history for private channels
- channels:read if you want public channel metadata access
- groups:read if you want private channel metadata access

Setup steps:

1. Create a Slack app in your workspace.
2. Add the read scopes above.
3. Install the app to the workspace.
4. Invite the bot to the channels you want to analyze.
5. Add the bot token and channel IDs to your `.env` file.
6. Run `python sprint_analyzer.py`.

## What the Analyzer Produces

The final report includes:

- Jira sprint status
- GitHub PR summary
- CI/CD health signals
- Slack weekly summary
- Groq-generated risks and recommendations

Example Slack summary output:

- total messages reviewed
- issue mentions
- success mentions
- delivery-risk mentions
- representative message snippets
- grouped Slack summaries by channel and by date

## Environment Example

Use [.env.example](.env.example) as the template for your local `.env` file.

It includes:

- Jira, GitHub, and Groq settings
- Slack API variables

## Environment Setup

Make sure these core variables already exist in your `.env` file:

```env
JIRA_SERVER=
JIRA_EMAIL=
JIRA_TOKEN=
GH_TOKEN=
GH_REPO=
GROQ_API_KEY=
```

Security hardening:

```env
# Optional but recommended for production
DELIVERY_HEALTH_API_KEY=
```

If `DELIVERY_HEALTH_API_KEY` is set, include header `x-api-key` when calling:

- `GET /health-report`
- `GET /metrics/dashboard`

Runtime compatibility:

- Recommended Python version: `3.12`
- Supported range for AI insights path: `3.10` to `3.14`
- If runtime is outside the supported range, the analyzer still runs but AI insights are skipped with a clear message.

## Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Use pinned interpreter:

```bash
python --version
# should be 3.12.x
```

Run analyzer:

```bash
python sprint_analyzer.py
```

## Agentic Mode

The project now supports a modular agentic engine in `agentic_engine.py`.

Use:

- `/health-report?agent_mode=true` (plan only)
- `/health-report?agent_mode=true&agent_execute=true` (plan + execute)

When enabled, the response includes an `agent` object containing:

- iterative planning cycles
- observed delivery-risk facts
- prioritized action queue (`P0` to `P3`)
- execution status tracking (`executed`, `skipped`, `failed`, `dry_run`)

Execution routing:

- Set `AGENT_ALERT_WEBHOOK_URL` in `.env` to post action alerts to Slack/Chat webhooks.
- If webhook is not configured, execution results are still tracked and returned in response.
- When execution is enabled, explicit alerts are generated for build failures, UAT risk states, and PROD risk states.

Autonomous escalation/retry logic:

- If a previously executed action remains `unresolved` or `regressed` after 24h, the agent auto-generates a `P0` escalation action.
- Escalation execution attempts:
	- Create Jira escalation ticket (requires `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_TOKEN`; optional `JIRA_ESCALATION_PROJECT`)
	- Notify senior stakeholders (`AGENT_STAKEHOLDER_WEBHOOK_URL`, falls back to `AGENT_ALERT_WEBHOOK_URL`)
- Escalation outcomes are included in tracking (`escalations_triggered`, `escalation_action_ids`).

Stateful memory (learning across runs):

- `AGENT_MEMORY_FILE` optional path for persisted agent history (default: `agent_memory_history.json` in project folder)
- `AGENT_MEMORY_MAX_RUNS` optional max stored runs (default: `60`)

The agent now stores each run's observed facts, action IDs, and execution outcomes, and uses recent run feedback to adjust next decisions.

It also computes trend memory between runs:

- `last_completion` vs `current_completion`
- `completion_delta` and a natural language performance summary (for example, improved by X%)
- resolved problem list when previous risks are now cleared

## n8n Cloud Delivery

For Manual Trigger -> HTTP Request -> Code -> Email -> Google Chat setup, see [N8N_SETUP.md](N8N_SETUP.md).

## Agentic Architecture

For a full professional overview of the end-to-end agent lifecycle, escalation model, tracking, memory, and operating guidance, see [AGENTIC_FLOW.md](AGENTIC_FLOW.md).

## Notes

- Slack data is optional.
- If Slack variables are missing, the analyzer still runs and reports that Slack is not configured.
- Keep Slack channels limited to delivery-relevant discussion so the weekly summary stays useful and accurate.

## Observability

Structured logs are emitted in JSON-like event format and metrics are available at:

- `GET /health` (includes metrics snapshot)
- `GET /metrics/dashboard`
- `GET /metrics/ui` (HTML dashboard with auto-refresh)

If API key auth is enabled, open dashboard with:

- `/metrics/ui?api_key=<your-key>`

Dashboard metrics include:

- run success rate
- external API failures (Jira/GitHub/Slack/Groq)
- action execution success
- unresolved outcome aging (avg/max hours)
