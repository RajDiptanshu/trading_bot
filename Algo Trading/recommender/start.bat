@echo off
REM ── NSE Recommendation Engine launcher ──────────────────────────
REM Uses the shared venv at C:\trading_bot\venv (create it per Notes\04 P0.1)
cd /d "%~dp0"

if exist "C:\trading_bot\venv\Scripts\python.exe" (
    set PY=C:\trading_bot\venv\Scripts\python.exe
) else (
    set PY=python
)

%PY% -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo Installing app dependencies into the venv...
    %PY% -m pip install -r requirements.txt
)

echo.
echo   Starting NSE Recommendation Engine at http://127.0.0.1:8650
echo   Press Ctrl+C to stop.
echo.
start "" http://127.0.0.1:8650
%PY% app.py
pause
