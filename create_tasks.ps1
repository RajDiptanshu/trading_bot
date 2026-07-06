# Create Trading Bot scheduled tasks in \TradingBot\ path
$logFile = "C:\trading_bot\logs\task_setup.log"
"$(Get-Date) - Starting full task creation" | Out-File $logFile
$python = "C:\trading_bot\venv\Scripts\python.exe"

function Create-Task($name, $script, $at, $args="") {
    try {
        $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d C:\trading_bot && `"$python`" $script $args >> C:\trading_bot\logs\$($name)_autorun.log 2>&1" -WorkingDirectory "C:\trading_bot"
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $at
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -WakeToRun
        Register-ScheduledTask -TaskName $name -TaskPath "\TradingBot\" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force
        "$(Get-Date) - Task $name created OK" | Out-File $logFile -Append
    } catch {
        "$(Get-Date) - Task $name FAILED: $_" | Out-File $logFile -Append
    }
}

# Task 1: MorningScan at 8:55 AM
Create-Task "MorningScan" "morning_scan.py" "08:55AM"

# Task 2: MorningCrew at 9:00 AM
Create-Task "MorningCrew" "morning_crew.py" "09:00AM"

# Task 3: ExecutionEngine at 9:16 AM
Create-Task "ExecutionEngine" "execution_engine.py" "09:16AM"

# Task 4: MidSentinel at 11:00 AM
Create-Task "MidSentinel" "midday_sentinel.py" "11:00AM"

# Task 5: PositionMonitor at 3:30 PM
Create-Task "PositionMonitor" "position_monitor.py" "03:30PM"

# Task 6: WeeklyTuner at 6:00 PM (Sundays only - custom trigger)
try {
    $action6 = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d C:\trading_bot && `"$python`" weekly_tuner.py >> C:\trading_bot\logs\tuner_autorun.log 2>&1" -WorkingDirectory "C:\trading_bot"
    $trigger6 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "06:00PM"
    Register-ScheduledTask -TaskName "WeeklyTuner" -TaskPath "\TradingBot\" -Action $action6 -Trigger $trigger6 -Settings $settings1 -RunLevel Limited -Force
    "$(Get-Date) - Task WeeklyTuner created OK" | Out-File $logFile -Append
} catch {
    "$(Get-Date) - Task WeeklyTuner FAILED: $_" | Out-File $logFile -Append
}

# Task 7: Watchdog (3 times daily)
try {
    $actionW = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d C:\trading_bot && `"$python`" watchdog.py >> C:\trading_bot\logs\watchdog_autorun.log 2>&1" -WorkingDirectory "C:\trading_bot"
    $t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:30AM"
    $t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "11:30AM"
    $t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "03:45PM"
    Register-ScheduledTask -TaskName "Watchdog" -TaskPath "\TradingBot\" -Action $actionW -Trigger @($t1, $t2, $t3) -RunLevel Limited -Force
    "$(Get-Date) - Task Watchdog created OK" | Out-File $logFile -Append
} catch {
    "$(Get-Date) - Task Watchdog FAILED: $_" | Out-File $logFile -Append
}

"$(Get-Date) - Done" | Out-File $logFile -Append
Get-ScheduledTask -TaskPath "\TradingBot\" | Format-List TaskName,State
