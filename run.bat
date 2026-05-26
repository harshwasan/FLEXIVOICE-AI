@echo off
REM Quick launcher for the FlexiLoans voice agent.
REM Goes through run_server.py so the Windows ProactorEventLoopPolicy is set
REM before uvicorn starts (required for the Claude SDK subprocess).

if not exist .venv\Scripts\activate.bat (
    echo .venv not found. Run: python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt
    exit /b 1
)

call .venv\Scripts\activate.bat
python run_server.py
