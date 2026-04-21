# Agentic Flow Architecture

## Executive Summary
This project implements a production-oriented delivery health agent that continuously evaluates sprint risk, decides interventions, executes operational actions, tracks outcomes, learns from historical runs, and escalates unresolved issues.

The flow is designed around a closed-loop control model:
Observe -> Analyze -> Decide -> Execute -> Track -> Learn -> Escalate -> Repeat

The system is currently implemented as a semi-autonomous operations agent with autonomous escalation behavior for unresolved outcomes after time-based observation windows.

## Objectives
- Detect delivery risk early from Jira, GitHub, CI/CD, and Slack signals.
- Convert risk into prioritized, owner-aware interventions.
- Execute notifications and escalation procedures automatically.
- Evaluate whether executed actions changed target metrics.
- Persist state across runs and adapt decisions using historical outcomes.
- Escalate unresolved risks to higher authority with structured evidence.

## System Scope
### In Scope
- Sprint risk synthesis and recommendation generation.
- Action planning with priorities P0 to P3.
- Alert execution through webhook channels.
- Outcome evaluation against previous run metrics.
- Time-based escalation and Jira escalation ticket creation.
- Memory persistence and trend learning across runs.
- n8n orchestration for delivery via email and chat.

### Out of Scope
- Direct auto-remediation of CI pipelines or deployment infrastructure.
- Automatic code changes and pull request generation.
- Guaranteed resolution validation without upstream system updates.

## Core Components
- Analyzer and report orchestrator: [sprint-mvp/sprint_analyzer.py](sprint_analyzer.py)
- API layer: [sprint-mvp/app/main.py](app/main.py)
- Agent engine: [sprint-mvp/agentic_engine.py](agentic_engine.py)
- n8n workflow template: [sprint-mvp/n8n_workflow_delivery_health.json](n8n_workflow_delivery_health.json)
- n8n setup guide: [sprint-mvp/N8N_SETUP.md](N8N_SETUP.md)

## Agentic Lifecycle
## 1. Observe
The analyzer collects operational signals from:
- Jira issue and sprint state
- GitHub pull requests and review latency
- CI/CD workflow and deployment state
- Slack delivery-relevant conversation summaries

Observed facts normalized in the agent include:
- blocked_over_4d
- blocked_total
- pending_review_over_48h
- pending_reviews
- build_failures
- last_build
- uat_state
- prod_state
- sprint_completion_pct

## 2. Analyze
The agent computes risk posture from observed facts and prior run memory:
- Current risk concentration
- Recent execution quality
- Recurring failure patterns
- Trend delta between previous and current completion
- Resolved versus unresolved operational problems

## 3. Decide
The decision engine produces a prioritized action queue with rich metadata:
- action_id
- priority
- owner
- objective
- reason
- success_criteria
- execute_message
- optional alert_type

Action classes include:
- Immediate risk alerts for build, UAT, and PROD risk states
- Delivery flow actions for blockers, review backlog, and scope control
- Execution routing actions when webhook delivery is repeatedly skipped

## 4. Execute
Execution mode runs when agent_execute is enabled.

Execution pathways:
- Webhook alert posting for operational and stakeholder notifications
- Escalation ticket creation in Jira for unresolved outcomes

Execution statuses:
- executed
- skipped
- failed
- dry_run

## 5. Track
Tracking captures both execution and outcome evidence.

Execution tracking captures:
- What action ran
- Status of execution
- Error or success detail
- Run timestamp and run id

Outcome tracking captures:
- Whether previous executed actions worked against measurable metrics
- Status labels: resolved, improving, pending_observation, unresolved, regressed, stable, not_evaluable
- Quantified evidence such as metric delta

## 6. Learn
The agent persists run history in a memory file and adjusts behavior with memory feedback.

Learning features:
- Last completion versus current completion delta
- Performance summary narrative
- Resolved problem detection
- Recurrent execution failure awareness
- Repeated skipped-delivery detection

## 7. Escalate
If prior actions remain unresolved or regressed after the observation window:
- The agent creates P0 escalation actions automatically
- It attempts Jira escalation ticket creation
- It notifies senior stakeholders via webhook
- Escalation identifiers are included in tracking output

## 8. Repeat
Each cycle reuses historical memory and current evidence to improve prioritization and intervention quality.

## Time-Based Outcome Policy
Outcome statuses follow an observation policy:
- Before 24 hours: unchanged issues are pending_observation
- At or after 24 hours: unchanged issues become unresolved
- Regressed issues trigger escalation logic immediately when evaluated at or after threshold

This policy prevents premature escalation while still enabling autonomous follow-through.

## Escalation Policy
Escalation triggers when all conditions are met:
- Prior action was executed
- Outcome status is unresolved or regressed
- Elapsed time since prior run is at least 24 hours

Escalation actions are generated as P0 and include:
- Original action reference
- Outcome evidence
- Age since action
- High-severity objective for governance visibility

## API Contract
Primary endpoint:
- GET /health-report

Key query parameters:
- include_ai_insights: includes language model narrative
- agent_mode: enables agent planning lifecycle
- agent_execute: enables action execution pathways

Response includes:
- risks and recommendations
- agent plan and action queue
- execution results
- outcome tracking
- escalation tracking
- state memory trend and learning summary

## n8n Orchestration Model
n8n workflow composes:
- Manual or scheduled trigger
- HTTP fetch from API endpoint
- Code node for report rendering
- Email send
- Optional chat delivery

The Code node should render all high-value sections:
- Action queue
- Action tracking
- Outcome tracking
- Outcome summary
- Escalation summary
- State memory and trend
- Execution summary

## Configuration and Environment Variables
Core operational variables:
- JIRA_SERVER
- JIRA_EMAIL
- JIRA_TOKEN
- GH_TOKEN
- GH_REPO
- GROQ_API_KEY

Slack and conversation ingestion variables:
- SLACK_BOT_TOKEN
- SLACK_CHANNEL_IDS
- SLACK_LOOKBACK_DAYS
- SLACK_MESSAGE_LIMIT

Agent execution and escalation variables:
- AGENT_ALERT_WEBHOOK_URL
- AGENT_STAKEHOLDER_WEBHOOK_URL
- JIRA_ESCALATION_PROJECT

Agent memory variables:
- AGENT_MEMORY_FILE
- AGENT_MEMORY_MAX_RUNS

## Governance and Safety Model
- Execution can be disabled through agent_execute mode.
- Missing webhooks degrade to tracked skipped behavior instead of hard failure.
- Escalation is evidence-backed and traceable to prior action and metric change.
- Every run is stamped with run id and timestamp for auditability.

## Operational Readiness Checklist
- API reachable from n8n endpoint.
- Webhook channels configured and validated.
- Jira credentials valid and project access verified.
- Memory file writable in runtime environment.
- n8n Code node using latest formatter sections.

## KPIs and Success Metrics
- Reduction in unresolved outcomes over rolling windows.
- Mean time from risk detection to first action execution.
- Escalation rate trend over time.
- Action effectiveness ratio:
  resolved or improving actions divided by executed actions.
- Delivery health trend via completion delta.

## Failure Modes and Diagnostics
Common failure patterns:
- No webhook configured leads to skipped executions.
- API not reloaded after env updates leads to stale behavior.
- n8n workflow not re-imported leads to outdated report formatting.
- Outcome tracking not visible on first run because baseline is not established.

Diagnostic anchors:
- execution.status_counts
- tracking.outcome_status_counts
- tracking.escalations_triggered
- memory.trend
- learning.next_focus

## Maturity Assessment
Current maturity level:
- Advanced semi-autonomous operational agent

Implemented capabilities:
- Observe, Analyze, Decide, Execute, Track, Learn, Escalate

Next high-value upgrades:
- Configurable observation windows per action type
- Policy-based approvals for selected escalation classes
- Multi-channel escalation routing by severity and domain
- Automated outcome feedback ingestion from external incident systems

## Professional Operating Model
For operational teams, this agent should be treated as:
- A continuously learning delivery operations co-pilot
- A risk-to-action execution engine with audit trail
- A governance-aware escalation assistant

In practice, this shifts delivery management from static reporting to adaptive operational intelligence.