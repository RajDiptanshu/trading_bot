# ============================================================================
#  Agent 6 — pre-market News Brain — Task Scheduler registration.
#  RUN ONCE AS ADMINISTRATOR:
#    powershell -ExecutionPolicy Bypass -File C:\trading_bot\setup_news_brain_task.ps1
#
#  CREATES (Mon-Fri, IST):
#    TradingBot\NewsBrain_Premarket  08:30  news_brain.py build
#      -> fetches last-24h news, runs the Claude contagion synthesis, writes
#         news_briefs\latest.json. Runs BEFORE the 09:25 Agent4 entry cycle so the
#         briefing is ready when the executor scans (agents.recommend reads it).
#
#  Idempotent: re-running removes the prior copy first (no duplicates).
#  Matches the existing Agent4 task conventions (cmd wrapper, venv python,
#  PYTHONIOENCODING=utf-8, cwd = recommender, logs in C:\trading_bot\logs\).
# ============================================================================

$logFile = "C:\trading_bot\logs\task_setup.log"
$python  = "C:\trading_bot\venv\Scripts\python.exe"
$recDir  = "C:\trading_bot\Algo Trading\recommender"
"$(Get-Date) - NewsBrain task registration starting" | Out-File $logFile -Append

# remove any previous copy (idempotent)
try { Unregister-ScheduledTask -TaskName "NewsBrain_Premarket" -TaskPath "\TradingBot\" -Confirm:$false -ErrorAction Stop } catch { }

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -WakeToRun

$arg = "/c set PYTHONIOENCODING=utf-8 && cd /d `"$recDir`" && `"$python`" news_brain.py build >> C:\trading_bot\logs\news_brain.log 2>&1"
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arg -WorkingDirectory $recDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:30AM

try {
    Register-ScheduledTask -TaskName "NewsBrain_Premarket" -TaskPath "\TradingBot\" -Action $action `
                           -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
    Write-Output "created \TradingBot\NewsBrain_Premarket  (08:30 Mon-Fri)"
    "$(Get-Date) - created NewsBrain_Premarket" | Out-File $logFile -Append
} catch {
    Write-Output "FAILED NewsBrain_Premarket : $_"
    "$(Get-Date) - FAILED NewsBrain_Premarket : $_" | Out-File $logFile -Append
}
