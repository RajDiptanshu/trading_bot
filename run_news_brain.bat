@echo off
REM ── Agent 6: pre-market News Brain — build today's 24h briefing on demand ──
REM Fetches the last 24h of news, runs the Claude contagion synthesis, and writes
REM news_briefs\news_brief_YYYY-MM-DD.json (+ latest.json). Takes ~1-2 minutes.
set PYTHONIOENCODING=utf-8
cd /d "%~dp0Algo Trading\recommender"
if exist "C:\trading_bot\venv\Scripts\python.exe" (
    set PY=C:\trading_bot\venv\Scripts\python.exe
) else (
    set PY=python
)
echo Building pre-market news briefing (this takes ~1-2 min)...
"%PY%" news_brain.py build
echo.
pause
