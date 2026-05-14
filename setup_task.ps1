# PowerShell script to create a scheduled task for the trading bot
# Run this script as Administrator to set up the hourly task

$taskName = "TradingBotHourly"
$scriptPath = "C:\Users\USER\OneDrive\Desktop\Futurestradingbot\run_bot.bat"
$workingDir = "C:\Users\USER\OneDrive\Desktop\Futurestradingbot"

# Check if task already exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Task '$taskName' already exists. Removing it first..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Create new scheduled task
$action = New-ScheduledTaskAction -Execute $scriptPath -WorkingDirectory $workingDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Runs the trading signal bot every hour"

Write-Host "Scheduled task '$taskName' created successfully!"
Write-Host "The bot will run every hour starting now."
Write-Host "You can manage this task in Task Scheduler (search for 'Task Scheduler' in Windows search)."