from datetime import datetime, timezone
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sprint_analyzer import generate_weekly_report, METRICS, bootstrap_metrics_from_agent_memory, configure_structured_logging, log_event
from delivery_intelligence import run_delivery_intelligence
from agentic_engine import _load_memory_state
from app.agent_bot import ask_delivery_bot

app = FastAPI(title="Delivery Health API", version="1.0.0")

# Setup templates and static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

configure_structured_logging()
bootstrap_metrics_from_agent_memory()


@app.get("/")
def read_root():
    return RedirectResponse(url="/chat")


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
            "charts": report.get("charts", {}),
            "weekly_slack_summary": report.get("slack", {}).get("week_summary", ""),
            "cicd": report.get("cicd", {}),
            "slack": report.get("slack", {}),
            "insights": output.get("insights"),
            "agent": output.get("agent"),
            "intelligence": report.get("intelligence"),
            "executed_actions": report.get("executed_actions"),
            "report_highlights": output.get("report_highlights"),
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

@app.get("/delivery-forecast")
def get_delivery_forecast(x_api_key: str | None = Header(default=None)):
    _validate_api_key(x_api_key)
    # Generate the latest health report
    report_output = generate_weekly_report()
    report = report_output.get("report", {})
    # Load agent memory state
    memory_state = _load_memory_state()
    # Run the delivery intelligence engine
    result = run_delivery_intelligence(report, memory_state)
    return result


# --- AI DELIVERY BOT ENDPOINTS ---


# Simple in-memory chat history for the bot
CHAT_HISTORY = []

@app.post("/api/chat")
async def chat_with_bot(payload: dict, x_api_key: str | None = Header(default=None)):
    global CHAT_HISTORY
    _validate_api_key(x_api_key)
    question = payload.get("question")
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")
    
    try:
        # Pass the current history to the bot
        response = ask_delivery_bot(question, history=CHAT_HISTORY)
        
        # Update history with the latest exchange
        CHAT_HISTORY.append({"role": "user", "content": question})
        CHAT_HISTORY.append({"role": "assistant", "content": response})
        
        # Keep history manageable
        if len(CHAT_HISTORY) > 10:
            CHAT_HISTORY = CHAT_HISTORY[-10:]
            
        return {"answer": response}
    except Exception as e:
        log_event("error", "bot_chat_failure", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/chat", response_class=HTMLResponse)
def chat_interface(request: Request):
    api_key = os.getenv("DELIVERY_HEALTH_API_KEY", "")
    return templates.TemplateResponse("chat.html", {"request": request, "api_key": api_key})
