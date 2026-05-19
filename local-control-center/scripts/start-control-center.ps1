param(
	[string]$DashboardHost = "127.0.0.1",
	[int]$DashboardPort = 4310,
	[int]$WorkerIntervalMs = 5000,
	[int]$WorkerCount = 2,
	[string]$Workspace = "",
	[string]$DbPath = "",
	[switch]$Worker,
	[switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..\..")
$StaticDir = Join-Path $ProjectRoot "local-control-center\dist\web"
$UvPath = (Get-Command uv).Source

if (-not $NoBuild -and -not (Test-Path -LiteralPath (Join-Path $StaticDir "index.html"))) {
	Push-Location $ProjectRoot
	try {
		corepack pnpm@10.24.0 run build:control-center
	} finally {
		Pop-Location
	}
}

if (-not (Test-Path -LiteralPath (Join-Path $StaticDir "index.html"))) {
	throw "Built dashboard not found at $StaticDir. Run pnpm run build:control-center first."
}

$env:OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA = "false"

Write-Host "Dashboard loopback URL: http://127.0.0.1:$DashboardPort"
Write-Host "Dashboard local DNS URL: http://local-control-center.test:$DashboardPort (run scripts/register-local-dns.ps1 once as Administrator)"

$arguments = @(
	"run",
	"python",
	"-m",
	"local_control_center",
	"--dashboard-host",
	$DashboardHost,
	"--dashboard-port",
	"$DashboardPort",
	"--worker-interval-ms",
	"$WorkerIntervalMs",
	"--worker-count",
	"$WorkerCount",
	"--static-dir",
	"`"$StaticDir`""
)

if ($Worker) {
	$arguments += "--worker"
}

if ($Workspace.Trim()) {
	$arguments += @("--workspace", "`"$Workspace`"")
}

if ($DbPath.Trim()) {
	$arguments += @("--db-path", "`"$DbPath`"")
}

Push-Location $ProjectRoot
	try {
		$pythonProcess = Start-Process `
		-FilePath $UvPath `
		-ArgumentList $arguments `
		-WorkingDirectory $ProjectRoot `
		-NoNewWindow `
		-PassThru
	try {
		Wait-Process -Id $pythonProcess.Id
		$pythonProcess.Refresh()
		exit $pythonProcess.ExitCode
	} finally {
		$pythonProcess.Refresh()
		if (-not $pythonProcess.HasExited) {
			Stop-Process -Id $pythonProcess.Id -Force
		}
	}
} finally {
	Pop-Location
}
