# n8n Cloud Weekly Report Flow

Use this architecture with n8n Cloud:

Manual Trigger
-> HTTP Request (FastAPI endpoint)
-> Code (format report)
-> Email
-> Google Chat (webhook)

## 1. Run FastAPI Locally

From `sprint-mvp`:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Check:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/health-report`
- `http://127.0.0.1:8000/health-report?include_ai_insights=true&agent_mode=true`
- `http://127.0.0.1:8000/health-report?include_ai_insights=true&agent_mode=true&agent_execute=true`

## 2. Expose API to n8n Cloud via ngrok

```bash
ngrok http 8000
```

Use the generated HTTPS URL, for example:

`https://abc123.ngrok-free.app/health-report?include_ai_insights=true&agent_mode=true&agent_execute=true`

## 3. n8n Nodes Configuration

## Node A: Manual Trigger

- Add `Manual Trigger`

## Node B: HTTP Request

- Method: `GET`
- URL: `https://YOUR-NGROK-URL/health-report?include_ai_insights=true&agent_mode=true&agent_execute=true`
- Response Format: `JSON`
- Agentic query parameters are embedded in the URL above.
- If `agent_execute=true`, action execution is attempted for each planned action.
- Set `AGENT_ALERT_WEBHOOK_URL` in `.env` for real alert posting; otherwise actions run in tracked skip mode.
- Agent run history is persisted to `AGENT_MEMORY_FILE` (default `agent_memory_history.json`) and used for memory-aware decisions.

## Node C: Code

Use this JavaScript in n8n Code node:

```javascript
const d = $json;

const risks = Array.isArray(d.risks) ? d.risks : [];
const recs = Array.isArray(d.recommendations) ? d.recommendations : [];
const actions = Array.isArray(d.agent?.action_queue) ? d.agent.action_queue : [];
const executed = Array.isArray(d.executed_actions) ? d.executed_actions : [];

const lines = [];
lines.push(`Weekly Delivery Health Report`);
lines.push(`Generated (UTC): ${d.generated_at_utc || "N/A"}`);
lines.push(`Sprint Completion: ${d.sprint_completion_pct ?? "N/A"}%`);
lines.push("");
lines.push("Risks detected:");
if (risks.length === 0) lines.push("- None");
for (const r of risks.slice(0, 5)) lines.push(`- ${r}`);
lines.push("");
lines.push("Recommendations:");
if (recs.length === 0) lines.push("- None");
for (const r of recs.slice(0, 3)) lines.push(`- ${r}`);
lines.push("");
lines.push("Agentic action queue (Top 3):");
if (actions.length === 0) lines.push("- No agent actions generated");
for (const a of actions.slice(0, 3)) {
  const pri = a.priority || "P3";
  const owner = a.owner || "Unassigned";
  const objective = a.objective || "No objective provided";
  lines.push(`- [${pri}] ${objective} (Owner: ${owner})`);
}
lines.push("");
lines.push("Execution status (Top 3):");
if (executed.length === 0) lines.push("- No execution records");
for (const e of executed.slice(0, 3)) {
  lines.push(`- ${e.action_id || "unknown"}: ${e.status || "unknown"} (${e.detail || "no detail"})`);
}
lines.push("");
lines.push("Weekly Slack summary:");
lines.push(d.weekly_slack_summary || "No Slack summary available.");
lines.push("");
lines.push("CI/CD:");
lines.push(`- Last Build: ${d.cicd?.last_build || "unknown"}`);
lines.push(`- UAT: ${d.cicd?.environments?.uat || "unknown"}`);
lines.push(`- PROD: ${d.cicd?.environments?.prod || "unknown"}`);

const reportText = lines.join("\n");

return [{
  json: {
    reportText,
    subject: `Weekly Delivery Health - ${new Date().toISOString().slice(0,10)}`,
  }
}];
```

## Node D: Email

- Subject: `{{$json.subject}}`
- Body: `{{$json.reportText}}`
- To: your team distribution list

## Node E: Google Chat (HTTP Request)

Create an incoming webhook in your Google Chat space and use its URL.

- Method: `POST`
- URL: `https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=...`
- Content-Type: `application/json`
- Body:

```json
{
  "text": "{{$json.reportText}}"
}
```

## 4. Make It Weekly

Replace Manual Trigger with Cron when ready:

- Cron: weekly (for example Monday 09:00)

## 5. Demo Tips

- Keep ngrok running during demo.
- Use Manual Trigger for live demo.
- Turn on Cron only after validating email and chat delivery.
