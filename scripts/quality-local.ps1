[CmdletBinding()]
param(
    [ValidateSet("fast", "story", "pr", "release")][string]$Tier = "pr",
    [string]$Base = "HEAD",
    [string[]]$PythonTest = @(),
    [string[]]$WebTest = @(),
    [string]$DbPath
)
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$QualityArguments = @("run", "python", "-m", "local_control_center.quality", "--tier", $Tier, "--base", $Base)
foreach ($TestFile in $PythonTest) { $QualityArguments += @("--python-test", $TestFile) }
foreach ($TestFile in $WebTest) { $QualityArguments += @("--web-test", $TestFile) }
if ($DbPath) { $QualityArguments += @("--db-path", $DbPath) }
Push-Location -LiteralPath $RepoRoot
try {
    & uv @QualityArguments
    $QualityExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $QualityExitCode
