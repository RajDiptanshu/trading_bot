@echo off
REM ============================================================
REM  Trading Bot - Task Scheduler Setup
REM  Run this ONCE as Administrator to register both tasks
REM ============================================================

echo Setting up Morning Scan task (8:55 AM, Mon-Fri)...
schtasks /create /tn "TradingBot\MorningScan" /tr "cmd /c set PYTHONIOENCODING=utf-8 && cd /d C:\trading_bot && \"C:\trading_bot\venv\Scripts\python.exe\" morning_scan.py >> C:\trading_bot\logs\scan_autorun.log 2>&1" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 08:55 /f /ru "%USERNAME%"
if %ERRORLEVEL% EQU 0 (echo   OK MorningScan created) else (echo   FAILED MorningScan - errorlevel %ERRORLEVEL%)

echo Setting up Morning Crew task (9:00 AM, Mon-Fri)...
schtasks /create /tn "TradingBot\MorningCrew" /tr "cmd /c set PYTHONIOENCODING=utf-8 && cd /d C:\trading_bot && \"C:\trading_bot\venv\Scripts\python.exe\" morning_crew.py >> C:\trading_bot\logs\crew_autorun.log 2>&1" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 09:00 /f /ru "%USERNAME%"
if %ERRORLEVEL% EQU 0 (echo   OK MorningCrew created) else (echo   FAILED MorningCrew - errorlevel %ERRORLEVEL%)

echo Setting up Watchdog tasks (9:30, 11:30, 15:45 Mon-Fri)...
schtasks /create /tn "TradingBot\Watchdog_930" /tr "cmd /c set PYTHONIOENCODING=utf-8 && cd /d C:\trading_bot && \"C:\trading_bot\venv\Scripts\python.exe\" watchdog.py >> C:\trading_bot\logs\watchdog_autorun.log 2>&1" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 09:30 /f /ru "%USERNAME%"
schtasks /create /tn "TradingBot\Watchdog_1130" /tr "cmd /c set PYTHONIOENCODING=utf-8 && cd /d C:\trading_bot && \"C:\trading_bot\venv\Scripts\python.exe\" watchdog.py >> C:\trading_bot\logs\watchdog_autorun.log 2>&1" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 11:30 /f /ru "%USERNAME%"
schtasks /create /tn "TradingBot\Watchdog_1545" /tr "cmd /c set PYTHONIOENCODING=utf-8 && cd /d C:\trading_bot && \"C:\trading_bot\venv\Scripts\python.exe\" watchdog.py >> C:\trading_bot\logs\watchdog_autorun.log 2>&1" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 15:45 /f /ru "%USERNAME%"

echo.
echo Verifying tasks...
schtasks /query /tn "TradingBot\MorningScan" /fo LIST 2>nul | findstr /i "Task Name\|Status\|Next Run"
schtasks /query /tn "TradingBot\MorningCrew" /fo LIST 2>nul | findstr /i "Task Name\|Status\|Next Run"

echo.
echo Done. Both tasks will run Mon-Fri.
echo IMPORTANT: PC must be ON and not sleeping at 8:55 AM / 9:00 AM.
echo To wake PC from sleep automatically, set a Wake Timer in Power Options.
pause
