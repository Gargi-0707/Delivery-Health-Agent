from datetime import datetime, timezone
import os
import time

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from sprint_analyzer import generate_weekly_report
from observability import METRICS, bootstrap_metrics_from_agent_memory, configure_structured_logging, log_event


app = FastAPI(title="Delivery Health API", version="1.0.0")
configure_structured_logging()
bootstrap_metrics_from_agent_memory()


def _validate_api_key(x_api_key):
    expected_key = os.getenv("DELIVERY_HEALTH_API_KEY", "").strip()
    if not expected_key:
        return
    if (x_api_key or "").strip() != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "delivery-health-api",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "metrics": METRICS.snapshot(),
    }


@app.get("/health-report")
def get_health_report(
    include_ai_insights: bool = True,
    agent_mode: bool = False,
    agent_execute: bool = False,
    x_api_key: str | None = Header(default=None),
):
    _validate_api_key(x_api_key)
    started = time.time()

    try:
        output = generate_weekly_report(
            include_ai_insights=include_ai_insights,
            agent_mode=agent_mode,
            agent_execute=agent_execute,
        )
        METRICS.record_run_success(output)

        report = output.get("report", {})

        compact = {
            "generated_at_utc": report.get("generated_at_utc"),
            "sprint_completion_pct": report.get("sprint_completion_pct"),
            "risks": report.get("signals", []),
            "recommendations": report.get("recommendations", []),
            "weekly_slack_summary": report.get("slack", {}).get("week_summary", ""),
            "cicd": report.get("cicd", {}),
            "slack": report.get("slack", {}),
            "insights": output.get("insights"),
            "agent": output.get("agent"),
            "executed_actions": report.get("executed_actions", []),
            "raw": report,
        }

        duration_ms = int((time.time() - started) * 1000)
        log_event(
            "info",
            "health_report_success",
            duration_ms=duration_ms,
            include_ai_insights=include_ai_insights,
            agent_mode=agent_mode,
            agent_execute=agent_execute,
        )
        return compact
    except Exception as exc:
        METRICS.record_run_failure()
        duration_ms = int((time.time() - started) * 1000)
        log_event(
            "error",
            "health_report_failure",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to generate report. Check server logs for details.")


@app.get("/metrics/dashboard")
def metrics_dashboard(x_api_key: str | None = Header(default=None)):
    _validate_api_key(x_api_key)
    return METRICS.snapshot()


@app.get("/metrics/ui", response_class=HTMLResponse)
def metrics_ui(
        x_api_key: str | None = Header(default=None),
        api_key: str | None = Query(default=None),
):
        _validate_api_key(x_api_key or api_key)

        initial = METRICS.snapshot()
        return f"""
<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Delivery Health Metrics</title>
    <style>
        :root {{
            --bg: #0c1418;
            --panel: #132028;
            --panel-alt: #1a2a34;
            --text: #e7f2f7;
            --muted: #92a7b2;
            --accent: #2ec4b6;
            --warn: #ff9f1c;
            --ok: #52d273;
            --bad: #ff6b6b;
            --ring: rgba(46, 196, 182, 0.35);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: "Segoe UI", "Trebuchet MS", sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 15% 10%, #1b3a42 0%, transparent 35%),
                radial-gradient(circle at 85% 90%, #1f2f46 0%, transparent 45%),
                var(--bg);
            min-height: 100vh;
        }}
        .wrap {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 24px;
        }}
        .hero {{
            margin-bottom: 18px;
            padding: 18px 20px;
            background: linear-gradient(120deg, #14313a, #1d2f3e);
            border: 1px solid #294654;
            border-radius: 16px;
            box-shadow: 0 12px 26px rgba(0, 0, 0, 0.22);
        }}
        h1 {{
            margin: 0 0 6px;
            font-size: 28px;
            letter-spacing: 0.4px;
        }}
        .meta {{
            color: var(--muted);
            font-size: 14px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 14px;
            margin-bottom: 14px;
        }}
        .card {{
            background: var(--panel);
            border: 1px solid #243b47;
            border-radius: 14px;
            padding: 14px;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.02);
        }}
        .title {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
        .value {{ font-size: 30px; font-weight: 700; margin-top: 8px; }}
        .sub {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
        .bar {{
            margin-top: 10px;
            height: 10px;
            background: #223742;
            border-radius: 999px;
            overflow: hidden;
        }}
        .fill {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent), #56e39f);
            transition: width 0.45s ease;
        }}
        .panel {{
            background: var(--panel-alt);
            border: 1px solid #26404f;
            border-radius: 14px;
            padding: 14px;
            margin-bottom: 14px;
        }}
        .list {{ margin: 8px 0 0; padding: 0; list-style: none; }}
        .list li {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px dashed #2f4b5a;
            padding: 9px 0;
            font-size: 14px;
        }}
        .list li:last-child {{ border-bottom: none; }}
        .badge {{
            min-width: 44px;
            text-align: center;
            border-radius: 999px;
            padding: 3px 10px;
            font-weight: 700;
            background: #223742;
        }}
        .err {{ background: rgba(255, 107, 107, 0.2); color: #ffb2b2; }}
        .ok {{ background: rgba(82, 210, 115, 0.2); color: #b6f1c5; }}
        .muted {{ color: var(--muted); }}
        @media (max-width: 700px) {{
            .value {{ font-size: 24px; }}
            h1 {{ font-size: 24px; }}
        }}
    </style>
</head>
<body>
    <div class=\"wrap\">
        <div class=\"hero\">
            <h1>Delivery Health Metrics Dashboard</h1>
            <div class=\"meta\" id=\"updated\">Loading...</div>
        </div>

        <div class=\"grid\">
            <div class=\"card\">
                <div class=\"title\">Run Success Rate</div>
                <div class=\"value\" id=\"runRate\">0%</div>
                <div class=\"sub\" id=\"runCounts\">0 successful / 0 total</div>
                <div class=\"bar\"><div class=\"fill\" id=\"runRateBar\" style=\"width:0%\"></div></div>
            </div>
            <div class=\"card\">
                <div class=\"title\">Action Execution Success</div>
                <div class=\"value\" id=\"actionRate\">0%</div>
                <div class=\"sub\" id=\"actionCounts\">0 executed / 0 attempted</div>
                <div class=\"bar\"><div class=\"fill\" id=\"actionRateBar\" style=\"width:0%\"></div></div>
            </div>
            <div class=\"card\">
                <div class=\"title\">Unresolved Outcome Aging</div>
                <div class=\"value\" id=\"unresolvedAvg\">0h</div>
                <div class=\"sub\" id=\"unresolvedMeta\">avg age | max 0h</div>
            </div>
            <div class=\"card\">
                <div class=\"title\">External API Failures</div>
                <div class=\"value\" id=\"externalTotal\">0</div>
                <div class=\"sub\">jira + github + slack + groq + other</div>
            </div>
        </div>

        <div class=\"panel\">
            <div class=\"title\">External API Failure Breakdown</div>
            <ul class=\"list\" id=\"apiList\"></ul>
        </div>

        <div class=\"panel\">
            <div class=\"title\">Live Mode</div>
            <div class=\"muted\">Auto-refresh every 10 seconds.</div>
        </div>
    </div>

    <script>
        const initialData = {initial};

        function setText(id, value) {{
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        }}

        function setWidth(id, pct) {{
            const el = document.getElementById(id);
            if (el) el.style.width = `${{Math.max(0, Math.min(100, Number(pct) || 0))}}%`;
        }}

        function render(data) {{
            const runs = data.runs || {{}};
            const action = data.action_execution || {{}};
            const aging = data.unresolved_outcome_aging_hours || {{}};
            const external = data.external_api_failures || {{}};

            const runRate = Number(runs.success_rate_pct || 0).toFixed(2);
            const actionRate = Number(action.success_pct || 0).toFixed(2);

            setText("runRate", `${{runRate}}%`);
            setText("runCounts", `${{runs.successful || 0}} successful / ${{runs.total || 0}} total`);
            setWidth("runRateBar", runRate);

            setText("actionRate", `${{actionRate}}%`);
            setText("actionCounts", `${{action.executed || 0}} executed / ${{action.attempted || 0}} attempted`);
            setWidth("actionRateBar", actionRate);

            setText("unresolvedAvg", `${{Number(aging.avg_hours || 0).toFixed(2)}}h`);
            setText("unresolvedMeta", `sample ${{aging.sample_count || 0}} | max ${{Number(aging.max_hours || 0).toFixed(2)}}h`);

            const totalExternal = Object.values(external).reduce((acc, val) => acc + Number(val || 0), 0);
            setText("externalTotal", String(totalExternal));
            setText("updated", `Last updated: ${{data.generated_at_utc || "unknown"}}`);

            const list = document.getElementById("apiList");
            list.innerHTML = "";
            Object.entries(external).forEach(([name, count]) => {{
                const li = document.createElement("li");
                li.innerHTML = `<span>${{name}}</span><span class=\"badge ${{Number(count) > 0 ? "err" : "ok"}}\">${{count}}</span>`;
                list.appendChild(li);
            }});
        }}

        async function refresh() {{
            try {{
                const query = new URLSearchParams(window.location.search);
                const key = query.get("api_key");
                const headers = key ? {{"x-api-key": key}} : {{}};
                const response = await fetch("/metrics/dashboard", {{headers}});
                if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
                const data = await response.json();
                render(data);
            }} catch (err) {{
                setText("updated", `Dashboard refresh failed: ${{err.message}}`);
            }}
        }}

        render(initialData);
        setInterval(refresh, 10000);
    </script>
</body>
</html>
"""
