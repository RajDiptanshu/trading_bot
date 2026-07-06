$events = Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -like '*WeeklyTuner*' -or $_.Message -like '*weekly_tuner*' -or $_.TaskDisplayName -like '*Weekly*' } |
    Select-Object -First 30 TimeCreated, Id, @{N='Msg';E={$_.Message.Substring(0,[Math]::Min(500,$_.Message.Length))}}

if (-not $events) {
    "No WeeklyTuner events found. Trying broader search..." | Out-File C:\trading_bot\ts_result.txt
    Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational' -ErrorAction SilentlyContinue |
        Select-Object -First 5 TimeCreated, Id, TaskDisplayName, Message |
        Format-List | Out-File C:\trading_bot\ts_result.txt -Append
} else {
    $events | Format-List | Out-File C:\trading_bot\ts_result.txt -Encoding UTF8
}
Write-Host "Output written to C:\trading_bot\ts_result.txt"
