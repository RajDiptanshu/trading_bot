# ============================================================================
#  Agent 5 — daily performance report — Task Scheduler registration.
#  RUN ONCE AS ADMINISTRATOR:
#    powershell -ExecutionPolicy Bypass -File C:\trading_bot\setup_agent5_task.ps1
#
#  CREATES (Mon-Fri, IST):
#    TradingBot\Agent5_Report  15:50  agent5_report.py build
#      -> runs AFTER Agent4_MonitorClose (15:45) so it captures the final book state.
#         Writes daily_reports\report_YYYY-MM-DD.json (+ report_latest.json) with the
#         go-live gate scorecard and a Claude EOD note. Builds the daily history.
#
#  Idempotent; mirrors the Agent4 / NewsBrain task conventions.
# ============================================================================

$logFile = "C:\trading_bot\logs\task_setup.log"
$python  = "C:\trading_bot\venv\Scripts\python.exe"
$recDir  = "C:\trading_bot\Algo Trading\recommender"
"$(Get-Date) - Agent5 task registration starting" | Out-File $logFile -Append

try { Unregister-ScheduledTask -TaskName "Agent5_Report" -TaskPath "\TradingBot\" -Confirm:$false -ErrorAction Stop } catch { }

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -WakeToRun

$arg = "/c set PYTHONIOENCODING=utf-8 && cd /d `"$recDir`" && `"$python`" agent5_report.py build >> C:\trading_bot\logs\agent5_report.log 2>&1"
$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arg -WorkingDirectory $recDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 3:50PM

try {
    Register-ScheduledTask -TaskName "Agent5_Report" -TaskPath "\TradingBot\" -Action $action `
                           -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
    Write-Output "created \TradingBot\Agent5_Report  (15:50 Mon-Fri)"
    "$(Get-Date) - created Agent5_Report" | Out-File $logFile -Append
} catch {
    Write-Output "FAILED Agent5_Report : $_"
    "$(Get-Date) - FAILED Agent5_Report : $_" | Out-File $logFile -Append
}
