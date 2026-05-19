param(
	[string]$TaskName = "LocalControlCenterAutonomousWorker",
	[int]$DashboardPort = 4310,
	[int]$WorkerIntervalMs = 5000,
	[int]$WorkerCount = 2,
	[string]$Workspace = ""
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..\..")
$PowerShellPath = (Get-Command powershell).Source
$StartScript = Join-Path $ProjectRoot "local-control-center\scripts\start-control-center.ps1"

if (-not (Test-Path -LiteralPath $StartScript)) {
	throw "Python control center start script not found at $StartScript."
}

$arguments = @(
	"-NoProfile",
	"-ExecutionPolicy",
	"Bypass",
	"-File",
	"`"$StartScript`"",
	"-Worker",
	"-WorkerIntervalMs",
	"$WorkerIntervalMs",
	"-WorkerCount",
	"$WorkerCount",
	"-DashboardHost",
	"127.0.0.1",
	"-DashboardPort",
	"$DashboardPort",
	"-NoBuild"
)

if ($Workspace.Trim()) {
	$arguments += @("-Workspace", "`"$Workspace`"")
}

$action = New-ScheduledTaskAction `
	-Execute $PowerShellPath `
	-Argument ($arguments -join " ") `
	-WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
	-AllowStartIfOnBatteries `
	-DontStopIfGoingOnBatteries `
	-ExecutionTimeLimit (New-TimeSpan -Days 7) `
	-RestartCount 3 `
	-RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
	-TaskName $TaskName `
	-Action $action `
	-Trigger $trigger `
	-Settings $settings `
	-Description "Starts the Python Local Control Center dashboard and autonomous worker for the current user." `
	-Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' for $PowerShellPath $($arguments -join ' ')"
