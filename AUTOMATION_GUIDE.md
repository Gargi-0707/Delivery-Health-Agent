# Automation & Deployment Guide
## Ensuring 24/7 Delivery Intelligence

This guide explains how to automate the AI Delivery Health Agent so that it stays updated without manual intervention.

---

## 1. The Goal: "Set it and Forget it"
In a professional office environment, data should be fresh before the first person arrives. Automating the **Analyzer** ensures that:
*   **Morning Stand-ups** have accurate, overnight data.
*   **The Chat Bot** always has the latest "Ground Truth."
*   **Human Error** is eliminated (no one has to remember to run a command).

---

## 2. Windows Automation (Task Scheduler)
Windows uses the **Task Scheduler** to run periodic scripts.

### Step 1: Create a Batch Script (`refresh_bot.bat`)
Create a file in your project root named `refresh_bot.bat` with the following content:
```batch
@echo off
:: Navigate to your project folder
cd /d "C:\path\to\your\Delivery-Health-Agent"

:: Activate the Python environment
call .venv\Scripts\activate

:: Run the Analyzer
python sprint_analyzer.py --ai --agent

:: (Optional) Send output to a log file for debugging
echo Last run completed at %date% %time% >> run_log.txt
exit
```

### Step 2: Schedule the Task
1.  Open **Task Scheduler** (Search in Start Menu).
2.  Click **Create Basic Task**.
3.  **Name**: `Daily Delivery Bot Refresh`.
4.  **Trigger**: `Daily` at `08:00 AM`.
5.  **Action**: `Start a Program`.
6.  **Program/script**: Select your `refresh_bot.bat`.
7.  **Finish**.

---

## 3. Linux Automation (Crontab)
If your company uses a Linux server (Ubuntu/Debian/CentOS), use **Cron**.

### Step 1: Open the Crontab editor
```bash
crontab -e
```

### Step 2: Add the Daily Schedule
Add this line at the bottom of the file to run every day at 8:00 AM:
```bash
00 08 * * * cd /path/to/project && /path/to/project/.venv/bin/python sprint_analyzer.py --ai --agent >> /path/to/project/cron_log.txt 2>&1
```

---

## 4. Persistent Dashboard (24/7 Access)
While the **Analyzer** runs once a day, the **Dashboard** (FastAPI) must stay running 24/7.

### On Windows:
Open a dedicated terminal window and run:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
*(Setting host to `0.0.0.0` allows other people in the office to open the dashboard using your PC's IP address).*

### On Linux (Using PM2 or Systemd):
To keep the dashboard alive even if the server restarts:
```bash
# Using PM2
pm2 start "uvicorn app.main:app --host 0.0.0.0 --port 8000" --name delivery-bot
```

---

## 5. Summary of Architecture
| Component | Mode | Frequency | Purpose |
| :--- | :--- | :--- | :--- |
| **API Dashboard** | Persistent | 24/7 | Provides the UI and Chat Bot interface. |
| **Analyzer Engine** | Scheduled | Daily (8 AM) | Refreshes the `latest_full_report.json` with new data. |
| **Agent Memory** | Incremental | On Every Run | Tracks trends and improves AI coaching over time. |
