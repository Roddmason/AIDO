param(
    [string]$DashboardHost = "127.0.0.1",
    [int]$DashboardPort = 4310,
    [int]$WorkerIntervalMs = 5000,
    [ValidateSet(1)][int]$WorkerCount = 1,
    [string]$Workspace = "",
    [string]$DbPath = "",
    [ValidateSet("supervisor", "api", "worker")][string]$Mode = "supervisor",
    [switch]$Worker,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$LauncherPath = Join-Path $ScriptRoot "start_control_center.py"
$UvPath = (Get-Command uv -ErrorAction Stop).Source
if ($Worker) { $Mode = "supervisor" }

# Optional local-control-center.test alias: register-local-dns.ps1 manages it explicitly.
# Python owns both native children; this wrapper preserves argument boundaries and exit status.
$LauncherArguments = @(
    "run", "python", $LauncherPath,
    "--dashboard-host", $DashboardHost,
    "--dashboard-port", "$DashboardPort",
    "--worker-interval-ms", "$WorkerIntervalMs",
    "--worker-count", "$WorkerCount",
    "--mode", $Mode
)
if ($Workspace.Trim()) { $LauncherArguments += @("--workspace", $Workspace) }
if ($DbPath.Trim()) { $LauncherArguments += @("--db-path", $DbPath) }
if ($NoBuild) { $LauncherArguments += "--no-build" }

Push-Location $ProjectRoot
try {
    & $UvPath @LauncherArguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
