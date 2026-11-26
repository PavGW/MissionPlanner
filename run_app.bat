@echo off
rem MissionPlanner web app -> http://127.0.0.1:8080  (Ctrl+C to stop)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv && .venv\Scripts\python -m pip install -r app\requirements.txt
)
start "" http://127.0.0.1:8080
.venv\Scripts\python app\main.py
