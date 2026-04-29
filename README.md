# AI Delivery Health Agent
### Autonomous Engineering Intelligence & Delivery Risk Management

[![Architecture: Modular](https://img.shields.io/badge/Architecture-Modular-blue.svg)](https://github.com/Gargi-0707/Delivery-Health-Agent)
[![Engine: Agentic AI](https://img.shields.io/badge/Engine-Agentic_AI-orange.svg)](/BOT_ARCHITECTURE.md)
[![Status: Production Ready](https://img.shields.io/badge/Status-Production_Ready-green.svg)](/SETUP_GUIDE.md)

---

## 🚀 Executive Overview
The **AI Delivery Health Agent** is an enterprise-grade platform designed to transform raw engineering data into actionable delivery intelligence. By integrating **Jira**, **GitHub**, and **Slack**, the agent provides a unified view of project health, automates risk detection, and executes autonomous interventions to keep sprints on track.

This project has been modularized for high scalability, supporting large-scale "Office Data" volumes while maintaining low latency and high accuracy through an advanced **Heuristic RAG** pipeline.

---

## 🛠️ Key Capabilities

*   **Unified Delivery Visibility**: Real-time aggregation of Jira tickets, GitHub Pull Requests, and Slack sentiment.
*   **Deterministic Risk Scoring**: Proprietary algorithms for **Delivery Risk Score** and **Team Health Score** (0-100).
*   **Autonomous Agent Engine**: A 5-phase pipeline (**Observe → Analyze → Decide → Act → Learn**) that identifies blockers and triggers Slack/Jira escalations automatically.
*   **Strategic Coaching**: LLM-powered insights providing boardroom-ready summaries and long-term structural recommendations.
*   **Smart Context Filtering**: Advanced token management that prioritizes critical data, making the agent perfect for large corporate datasets (up to 10x standard volume).

---

## 🏗️ Project Architecture

The codebase follows a clean, package-based modular architecture:

*   **`core/`**: Shared infrastructure (Config, Logging, Metrics).
*   **`integrations/`**: Dedicated API clients for Jira, GitHub, and Slack.
*   **`intelligence/`**: The "Brain" – KPIs, Forecasts, and Risk calculations.
*   **`agents/`**: The execution pipeline for autonomous delivery management.
*   **`reports/`**: Data visualization and report orchestration.
*   **`app/`**: FastAPI-powered dashboard and interactive AI bot.

---

## 📚 Documentation Map

| Document | Description |
| :--- | :--- |
| [**SETUP_GUIDE.md**](/SETUP_GUIDE.md) | **Start Here.** Instructions for environment config, installation, and maintenance. |
| [**BOT_ARCHITECTURE.md**](/BOT_ARCHITECTURE.md) | Deep dive into the AI reasoning engine, Heuristic RAG, and cost analysis. |
| [**AGENTIC_FLOW.md**](/AGENTIC_FLOW.md) | Details on the autonomous decision-making cycles and escalation logic. |
| [**N8N_SETUP.md**](/N8N_SETUP.md) | Guide for cloud automation and external reporting via n8n. |

---

## ⚡ Quick Start

1.  **Configure Environment**: Copy `.env.example` to `.env` and add your Jira/GitHub/Slack/Groq credentials.
2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Launch the CLI**:
    ```bash
    python sprint_analyzer.py --ai --agent
    ```
4.  **Launch the Dashboard**:
    ```bash
    uvicorn app.main:app --reload
    ```

---

## 🌐 Tech Stack
*   **Language**: Python 3.12+
*   **AI Models**: Groq / Llama-3.3-70b
*   **Web Framework**: FastAPI / Vanilla CSS
*   **Integrations**: Jira REST API, GitHub GraphQL/REST, Slack Web API
*   **Visualization**: SVG-based dynamic charting

---

## 🛡️ Security & Maintenance
*   **Data Privacy**: No data is stored externally; all processing happens within your infrastructure.
*   **Token Security**: Supports API Key authentication and secure environment variable management.
*   **Health Monitoring**: Real-time metrics dashboard available at `/metrics/ui`.

---
© 2024 AI Delivery Health Agent Team. All Rights Reserved.
