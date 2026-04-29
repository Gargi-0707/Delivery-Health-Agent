from datetime import datetime, timezone
import os
import time

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from sprint_analyzer import generate_weekly_report, METRICS, bootstrap_metrics_from_agent_memory, configure_structured_logging, log_event
from delivery_intelligence import run_delivery_intelligence
from agentic_engine import _load_memory_state
from app.agent_bot import ask_delivery_bot




app = FastAPI(title="Delivery Health API", version="1.0.0")
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
def chat_interface():
    api_key = os.getenv("DELIVERY_HEALTH_API_KEY", "")
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Delivery Intelligence Bot</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --bg: #0f172a;
            --card-bg: rgba(30, 41, 59, 0.7);
            --primary: #6366f1;
            --primary-light: #818cf8;
            --text: #f8fafc;
            --text-dim: #94a3b8;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', sans-serif;
            background: radial-gradient(circle at top left, #1e1b4b, #0f172a);
            color: var(--text);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        header {
            padding: 1.5rem 2rem;
            background: rgba(15, 23, 42, 0.8);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .logo {
            font-weight: 600;
            font-size: 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            background: linear-gradient(90deg, var(--primary-light), #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        main {
            flex: 1;
            padding: 2rem;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            scroll-behavior: smooth;
        }

        /* Scrollbar */
        main::-webkit-scrollbar { width: 6px; }
        main::-webkit-scrollbar-track { background: transparent; }
        main::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }

        .message {
            max-width: 85%;
            padding: 1rem 1.25rem;
            border-radius: 1rem;
            line-height: 1.6;
            font-size: 0.95rem;
            animation: fadeIn 0.3s ease-out;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .user-msg {
            align-self: flex-end;
            background: var(--primary);
            color: white;
            border-bottom-right-radius: 0.25rem;
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
        }

        .bot-msg {
            align-self: flex-start;
            background: var(--card-bg);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-bottom-left-radius: 0.25rem;
        }

        .bot-msg h1, .bot-msg h2, .bot-msg h3 { margin-top: 1rem; margin-bottom: 0.5rem; font-size: 1.1rem; }
        .bot-msg ul, .bot-msg ol { padding-left: 1.5rem; margin-bottom: 1rem; }
        .bot-msg code { background: rgba(0,0,0,0.3); padding: 0.2rem 0.4rem; border-radius: 4px; font-family: monospace; }

        .typing {
            font-style: italic;
            color: var(--text-dim);
            font-size: 0.85rem;
            display: none;
        }

        footer {
            padding: 2rem;
            background: transparent;
        }

        .input-container {
            max-width: 800px;
            margin: 0 auto;
            position: relative;
            background: var(--card-bg);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 1.5rem;
            padding: 0.5rem;
            display: flex;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            transition: border-color 0.3s;
        }

        .input-container:focus-within {
            border-color: var(--primary);
        }

        input {
            flex: 1;
            background: transparent;
            border: none;
            color: white;
            padding: 0.75rem 1rem;
            font-size: 1rem;
            outline: none;
        }

        button {
            background: var(--primary);
            color: white;
            border: none;
            padding: 0.75rem 1.5rem;
            border-radius: 1rem;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
        }

        button:hover {
            background: var(--primary-light);
            transform: scale(1.05);
        }

        button:active {
            transform: scale(0.95);
        }

        .initial-suggestions {
            display: flex;
            gap: 0.75rem;
            justify-content: center;
            margin-bottom: 1rem;
            flex-wrap: wrap;
        }

        .suggestion-chip {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            padding: 0.5rem 1rem;
            border-radius: 2rem;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
            color: var(--text-dim);
        }

        .suggestion-chip:hover {
            background: rgba(255,255,255,0.1);
            border-color: var(--primary);
            color: white;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Delivery Intelligence Bot
        </div>
        <div style="font-size: 0.8rem; color: var(--text-dim);">V1.0.0 (AI Agent Mode)</div>
    </header>

    <main id="chat-box">
        <div class="message bot-msg">
            Hello! I am your AI Delivery Intelligence Assistant. I have indexed your current sprint, backlog, GitHub PRs, and Slack conversations. 
            <br><br>
            What would you like to know about the project today?
        </div>
    </main>

    <footer>
        <div class="initial-suggestions" id="suggestions">
            <div class="suggestion-chip" onclick="ask('How is the current sprint progress?')">Sprint Progress</div>
            <div class="suggestion-chip" onclick="ask('What are the top delivery risks right now?')">Top Risks</div>
            <div class="suggestion-chip" onclick="ask('Analyze PR review bottlenecks')">PR Bottlenecks</div>
            <div class="suggestion-chip" onclick="ask('When will we finish the project?')">Delivery Forecast</div>
        </div>
        <div class="input-container">
            <input type="text" id="user-input" placeholder="Ask about your project delivery..." onkeypress="if(event.key === 'Enter') sendMessage()">
            <button onclick="sendMessage()">Send</button>
        </div>
        <div class="typing" id="typing-indicator" style="margin-top: 1rem; text-align: center;">AI is analyzing project data...</div>
    </footer>

    <script>
        const chatBox = document.getElementById('chat-box');
        const userInput = document.getElementById('user-input');
        const typingIndicator = document.getElementById('typing-indicator');
        const API_KEY = "{api_key}";

        function appendMessage(role, content) {
            const div = document.createElement('div');
            div.className = `message ${role}-msg`;
            
            if (role === 'bot') {
                div.innerHTML = marked.parse(content);
            } else {
                div.textContent = content;
            }
            
            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        async function ask(question) {
            userInput.value = question;
            sendMessage();
        }

        async function sendMessage() {
            const question = userInput.value.trim();
            if (!question) return;

            appendMessage('user', question);
            userInput.value = '';
            document.getElementById('suggestions').style.display = 'none';
            
            typingIndicator.style.display = 'block';

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'x-api-key': API_KEY
                    },
                    body: JSON.stringify({ question })
                });

                if (response.status === 401) throw new Error('Unauthorized: Missing or invalid API Key');
                if (!response.ok) throw new Error('Failed to reach AI engine');

                const data = await response.json();
                appendMessage('bot', data.answer);
            } catch (error) {
                appendMessage('bot', '❌ Error: ' + error.message);
            } finally {
                typingIndicator.style.display = 'none';
            }
        }
    </script>
</body>
</html>
    """
    return html_content.replace("{api_key}", api_key)
