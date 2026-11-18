[CmdletBinding()]
param([string]$DbPath)
$ErrorActionPreference = "Stop"
$VerificationRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VerificationArguments = @("run", "--no-sync", "python", "-m", "local_control_center.quality.verify")
if ($DbPath) { $VerificationArguments += @("--db-path", $DbPath) }
Push-Location -LiteralPath $VerificationRoot
try {
    & uv @VerificationArguments
    $VerificationExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $VerificationExitCode
