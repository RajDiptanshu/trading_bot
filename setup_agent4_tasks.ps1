# ============================================================================
#  Agent 4 — Task Scheduler migration.  RUN ONCE AS ADMINISTRATOR:
#    powershell -ExecutionPolicy Bypass -File C:\trading_bot\setup_agent4_tasks.ps1
#
#  REMOVES the old V9 pipeline tasks (replaced by the recommender + Agent 4):
#    MorningScan, MorningCrew, ExecutionEngine, MidSentinel, PositionMonitor,
#    WeeklyTuner, Watchdog(+_930/_1130/_1545) — in BOTH historical locations
#    (\TradingBot\ folder and root-level TradingBot_* names). No duplicates left.
#
#  CREATES the new flow (Mon-Fri, IST market hours):
#    TradingBot\Agent4_EntryCycle     09:25  agent4.py execute  (entries)
#    TradingBot\Agent4_MonitorMidday  12:30  agent4.py monitor  (stops/targets/trail)
#    TradingBot\Agent4_MonitorClose   15:45  agent4.py monitor  (end-of-day management)
#    TradingBot\ML_WeeklyRetrain      Sun 18:00  train_ml.py    (refresh ML signal)
# ============================================================================

$logFile = "C:\trading_bot\logs\task_setup.log"
$python  = "C:\trading_bot\venv\Scripts\python.exe"
$recDir  = "C:\trading_bot\Algo Trading\recommender"
"$(Get-Date) - Agent4 task migration starting" | Out-File $logFile -Append

# ---------- 1. remove every old task, wherever it lives ----------
$oldFolder = @("MorningScan","MorningCrew","ExecutionEngine","MidSentinel",
               "PositionMonitor","WeeklyTuner","Watchdog",
               "Watchdog_930","Watchdog_1130","Watchdog_1545")
$oldRoot   = @("TradingBot_MorningScan","TradingBot_MorningCrew","TradingBot_ExecutionEngine",
               "TradingBot_MidSentinel","TradingBot_PositionMonitor","TradingBot_WeeklyTuner",
               "TradingBot_Watchdog")
foreach ($n in $oldFolder) {
    try { Unregister-ScheduledTask -TaskName $n -TaskPath "\TradingBot\" -Confirm:$false -ErrorAction Stop
          Write-Output "removed \TradingBot\$n"; "$(Get-Date) - removed \TradingBot\$n" | Out-File $logFile -Append }
    catch { }
}
foreach ($n in $oldRoot) {
    try { Unregister-ScheduledTask -TaskName $n -TaskPath "\" -Confirm:$false -ErrorAction Stop
          Write-Output "removed \$n"; "$(Get-Date) - removed \$n" | Out-File $logFile -Append }
    catch { }
}
# also remove any previous copies of the NEW names (idempotent re-runs, no duplicates)
foreach ($n in @("Agent4_EntryCycle","Agent4_MonitorMidday","Agent4_MonitorClose","ML_WeeklyRetrain","Agent4_Watchdog","Agent4_Cycle30")) {
    try { Unregister-ScheduledTask -TaskName $n -TaskPath "\TradingBot\" -Confirm:$false -ErrorAction Stop } catch { }
}

# ---------- 2. register the new flow ----------
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -WakeToRun

function New-A4Task($name, $script, $cliArg, $days, $at, $logName) {
    try {
        $arg = "/c set PYTHONIOENCODING=utf-8 && cd /d `"$recDir`" && `"$python`" $script $cliArg >> C:\trading_bot\logs\$logName.log 2>&1"
        $action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arg -WorkingDirectory $recDir
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $at
        Register-ScheduledTask -TaskName $name -TaskPath "\TradingBot\" -Action $action -Trigger $trigger `
                               -Settings $settings -RunLevel Limited -Force | Out-Null
        Write-Output "created \TradingBot\$name  ($at $($days -join ','))"
        "$(Get-Date) - created $name" | Out-File $logFile -Append
    } catch {
        Write-Output "FAILED $name : $_"
        "$(Get-Date) - FAILED $name : $_" | Out-File $logFile -Append
    }
}

$wk = @("Monday","Tuesday","Wednesday","Thursday","Friday")

# Agent4_Cycle30 — every 30 min, 09:25 to 15:25 (13 runs/day): exits first, then entries.
# The script itself refuses to act outside 09:15-15:35, so stray fires are harmless.
try {
    $arg = "/c set PYTHONIOENCODING=utf-8 && cd /d `"$recDir`" && `"$python`" agent4.py cycle >> C:\trading_bot\logs\agent4_cycle.log 2>&1"
    $action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $arg -WorkingDirectory $recDir
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $wk -At "09:25AM"
    $rep     = New-ScheduledTaskTrigger -Once -At "09:25AM" `
                 -RepetitionInterval (New-TimeSpan -Minutes 30) `
                 -RepetitionDuration (New-TimeSpan -Hours 6)
    $trigger.Repetition = $rep.Repetition
    Register-ScheduledTask -TaskName "Agent4_Cycle30" -TaskPath "\TradingBot\" -Action $action `
                           -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
    Write-Output "created \TradingBot\Agent4_Cycle30  (09:25AM every 30 min x6h, Mon-Fri)"
    "$(Get-Date) - created Agent4_Cycle30" | Out-File $logFile -Append
} catch {
    Write-Output "FAILED Agent4_Cycle30 : $_"
    "$(Get-Date) - FAILED Agent4_Cycle30 : $_" | Out-File $logFile -Append
}

New-A4Task "Agent4_MonitorClose"  "agent4.py" "monitor" $wk        "03:45PM" "agent4_monitor"
New-A4Task "Agent4_Watchdog"      "agent4_watchdog.py" "" $wk      "04:05PM" "watchdog"
New-A4Task "ML_WeeklyRetrain"     "train_ml.py" ""      @("Sunday") "06:00PM" "ml_retrain"

# ---------- 3. show the final state ----------
Write-Output ""
Write-Output "Final TradingBot schedule:"
Get-ScheduledTask -TaskPath "\TradingBot\" -ErrorAction SilentlyContinue |
    Select-Object TaskName, State | Format-Table -AutoSize
Write-Output "Anything still at root level (should list nothing):"
Get-ScheduledTask -TaskPath "\" -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like "TradingBot*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
"$(Get-Date) - migration done" | Out-File $logFile -Append
Write-Output "Done. PC must be awake at 09:25 / 12:30 / 15:45 on trading days (WakeToRun is set)."
