param(
	[switch]$WithFaiss,
	[switch]$SkipNodeSetup,
	[switch]$SkipPlaywright
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..")

function Require-Command {
	param(
		[string]$Name,
		[string]$InstallHint
	)

	if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
		throw "$Name is required. $InstallHint"
	}
}

function Invoke-Step {
	param(
		[string]$Name,
		[scriptblock]$Block
	)

	Write-Host ""
	Write-Host "==> $Name"
	& $Block
}

Push-Location $ProjectRoot
try {
	$nodeHelper = Join-Path $ProjectRoot "local-control-center\scripts\use-node.ps1"
	$isWindowsHost = [System.Environment]::OSVersion.Platform -eq "Win32NT"
	$hasNvm = $null -ne (Get-Command nvm -ErrorAction SilentlyContinue)

	if (-not $SkipNodeSetup -and $isWindowsHost -and $hasNvm -and (Test-Path -LiteralPath $nodeHelper)) {
		Invoke-Step "Select the repository Node runtime" {
			& $nodeHelper
		}
	} elseif (-not $SkipNodeSetup -and $isWindowsHost -and -not $hasNvm) {
		Write-Host "nvm was not found; using the current node on PATH. Ensure it matches .nvmrc before release-grade checks."
	} elseif (-not $SkipNodeSetup) {
		Write-Host "Skipping Node version helper. Ensure node matches .nvmrc before running quality checks."
	}

	Require-Command "node" "Install Node 24.16.x or use nvm with the repository .nvmrc."
	Require-Command "corepack" "Corepack is bundled with supported Node releases."
	Require-Command "uv" "Install uv from https://docs.astral.sh/uv/."

	Invoke-Step "Install JavaScript dependencies" {
		corepack enable
		corepack pnpm@10.24.0 install
	}

	$uvArgs = @("sync", "--extra", "dev", "--extra", "test")
	if ($WithFaiss) {
		$uvArgs += @("--extra", "faiss")
	}

	Invoke-Step "Install Python environment" {
		& uv @uvArgs
	}

	if (-not $SkipPlaywright) {
		Invoke-Step "Install Playwright browsers" {
			corepack pnpm@10.24.0 exec playwright install
		}
	}

	Write-Host ""
	Write-Host "AIDO local dependencies are ready."
	Write-Host "Start the control center with: corepack pnpm@10.24.0 run start"
} finally {
	Pop-Location
}
