param(
	[string]$TaskName = "LocalControlCenterAutonomousWorker"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
	Write-Host "Scheduled task '$TaskName' is not registered."
	return
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Unregistered scheduled task '$TaskName'."
