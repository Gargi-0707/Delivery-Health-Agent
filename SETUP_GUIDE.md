# Enterprise Setup & Maintenance Guide
## AI Delivery Health Agent

This guide provides comprehensive instructions for setting up, running, and maintaining the AI Delivery Health Agent in a professional corporate environment.

---

## 1. Project Overview
The AI Delivery Health Agent is a modular, autonomous system that monitors engineering delivery health. It extracts data from Jira, GitHub, and Slack, performs deep KPI analysis, and uses AI (Groq/Llama-3) to provide strategic coaching and autonomous interventions.

---

## 2. Prerequisites
*   **Python**: Version 3.10 or higher.
*   **Git**: For version control and deployment.
*   **Infrastructure**: A server or VM (e.g., AWS EC2, Azure VM) with at least 2GB RAM.
*   **Network**: Outbound access to `api.atlassian.com`, `api.github.com`, `slack.com`, and `api.groq.com`.

---

## 3. Environment Configuration (`.env`)
You must create a `.env` file in the project root with the following keys:

### A. Jira Integration
*   `JIRA_SERVER`: Your Jira instance URL (e.g., `https://yourcompany.atlassian.net`).
*   `JIRA_EMAIL`: The email of the service account.
*   `JIRA_TOKEN`: API Token generated from Atlassian Account Settings.

### B. GitHub Integration
*   `GH_TOKEN`: Personal Access Token (PAT) with `repo` and `read:org` scopes.
*   `GH_REPO`: The full repository name (e.g., `org/repo-name`).

### C. Slack Integration (Bot)
*   `SLACK_BOT_TOKEN`: Bot User OAuth Token (starts with `xoxb-`).
*   `SLACK_CHANNEL_IDS`: Comma-separated IDs of channels to monitor (e.g., `C123,C456`).
*   `AGENT_ALERT_WEBHOOK_URL`: Webhook for the bot to post delivery alerts.

### D. AI Engine (Groq)
*   `GROQ_API_KEY`: Your Groq Cloud API Key.
*   `GROQ_MODEL`: Recommended: `llama-3.3-70b-versatile`.

---

## 4. Installation Steps

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/Gargi-0707/Delivery-Health-Agent.git
    cd Delivery-Health-Agent
    ```

2.  **Set up Virtual Environment**:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Verify Configuration**:
    Run a test report to ensure connectivity:
    ```bash
    python sprint_analyzer.py
    ```

---

## 5. How It Works (The Core Loop)
The agent follows an **Observe → Analyze → Intelligence → Decide → Act** lifecycle:

1.  **Observe**: Fetches raw data from Jira, GitHub, and Slack via the `integrations/` layer.
2.  **Analyze**: Processes raw data into a structured Sprint Report (JSON).
3.  **Intelligence**: The `intelligence/` engine calculates deterministic KPIs like **Delivery Risk Score** and **Team Health Score**.
4.  **Decide**: The `engine/planner.py` uses AI to compare the report against the **Action Catalog** to find necessary interventions.
5.  **Act**: If enabled, the agent executes actions (Slack alerts, Jira tickets) via the `agents/act.py` module.

---

## 6. Maintenance & Operations

### A. Rotating Tokens (Every 90 Days)
For security, tokens for Jira, GitHub, and Slack should be rotated every 90 days. Update the `.env` file and restart the service.

### B. Monitoring the Action Catalog
The `engine/catalog.py` contains the "rules" the bot follows. As your company processes change, you should update this file to:
*   Add new types of alerts.
*   Change priority levels.
*   Modify who "owns" specific project risks.

### C. Managing Agent Memory
The agent stores history in `agent_memory_history.json`. 
*   **Backup**: Include this file in your weekly server backups.
*   **Clean-up**: If the file grows too large (>50MB), the system will automatically prune old runs, but you can manually archive it if needed.

### D. Updating Intelligence Rules
If your company calculates "Risk" differently (e.g., you care more about PR rework than blocked tasks), you can adjust the weights in `intelligence/risk_score.py`.

---

## 7. Troubleshooting
*   **"Rate Limit" Errors**: This happens if the AI is asked too many questions. The bot has built-in exponential backoff and will retry automatically.
*   **Empty Reports**: Check your `JIRA_SERVER` URL and ensure the user has access to the specific Project and Sprint.
*   **Bot not answering Slack**: Ensure the Bot Token is correct and the bot has been invited (`/invite @botname`) to the Slack channels.
