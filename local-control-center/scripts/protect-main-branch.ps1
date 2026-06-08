Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$replacement = Join-Path $PSScriptRoot "protect-repository-branches.ps1"
Write-Warning "DEPRECATED: protect-main-branch.ps1 has been renamed to protect-repository-branches.ps1. Removal date: 2026-09-01."
& $replacement @args
exit $LASTEXITCODE
