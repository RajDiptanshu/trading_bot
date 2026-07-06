# Run this script AS ADMINISTRATOR to point all TradingBot scheduled tasks
# at the dedicated venv interpreter (P0.1 fix).

$venvPython = "C:\trading_bot\venv\Scripts\python.exe"

# Tasks invoked via cmd.exe with "python -X utf8 <script> >> <log> 2>&1"
$cmdTasks = @(
  "TradingBot_ExecutionEngine",
  "TradingBot_MidSentinel",
  "TradingBot_MorningCrew",
  "TradingBot_PositionMonitor",
  "TradingBot_WeeklyTuner"
)

foreach ($name in $cmdTasks) {
    $task = Get-ScheduledTask -TaskName $name -TaskPath "\"
    $oldArgs = $task.Actions[0].Arguments
    $newArgs = $oldArgs -replace "python -X utf8", "`"$venvPython`" -X utf8"
    $newAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $newArgs -WorkingDirectory $task.Actions[0].WorkingDirectory
    Set-ScheduledTask -TaskName $name -TaskPath "\" -Action $newAction | Out-Null
    Write-Output "$name -> $newArgs"
}

# TradingBot_MorningScan: Execute=python directly (no cmd.exe wrapper)
$task = Get-ScheduledTask -TaskName "TradingBot_MorningScan" -TaskPath "\"
$newAction = New-ScheduledTaskAction -Execute $venvPython -Argument $task.Actions[0].Arguments -WorkingDirectory $task.Actions[0].WorkingDirectory
Set-ScheduledTask -TaskName "TradingBot_MorningScan" -TaskPath "\" -Action $newAction | Out-Null
Write-Output "TradingBot_MorningScan -> $venvPython $($task.Actions[0].Arguments)"

Write-Output ""
Write-Output "Done. Verify with:"
Write-Output "Get-ScheduledTask | Where-Object {`$_.TaskName -match 'TradingBot'} | ForEach-Object { `$_.Actions[0] }"
