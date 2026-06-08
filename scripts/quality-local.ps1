$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PackagePath = Join-Path $RepoRoot "package.json"

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "package.json was not found at $PackagePath"
}

$Package = Get-Content -Raw -LiteralPath $PackagePath | ConvertFrom-Json
$ScriptNames = @($Package.scripts.PSObject.Properties.Name)

function Invoke-QualityScript {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [switch]$Optional
    )

    if ($ScriptNames -notcontains $Script) {
        if ($Optional) {
            Write-Host "SKIP ${Name}: package script '$Script' is not declared."
            return
        }
        throw "Required quality script '$Script' is not declared."
    }

    Write-Host "==> $Name"
    & corepack pnpm@10.24.0 run $Script
    if ($LASTEXITCODE -ne 0) {
        throw "Quality step failed: $Name ($Script), exit code $LASTEXITCODE"
    }
}

Set-Location -LiteralPath $RepoRoot

Invoke-QualityScript -Name "Productive truth scanner" -Script "quality:productive-truth"
Invoke-QualityScript -Name "Python tests" -Script "test:py"
Invoke-QualityScript -Name "Web tests" -Script "test:web"
Invoke-QualityScript -Name "Frontend build" -Script "build:control-center"
Invoke-QualityScript -Name "Web typecheck" -Script "typecheck:web" -Optional
Invoke-QualityScript -Name "Lint" -Script "lint" -Optional
Invoke-QualityScript -Name "Architecture guardrails" -Script "quality:architecture"
Invoke-QualityScript -Name "Secret scan" -Script "security:secrets"
Invoke-QualityScript -Name "SAST" -Script "security:sast"

Write-Host "Local quality gate completed."
