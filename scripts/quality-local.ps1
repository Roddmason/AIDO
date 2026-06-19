$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PackagePath = Join-Path $RepoRoot "package.json"

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "package.json was not found at $PackagePath"
}

$Package = Get-Content -Raw -LiteralPath $PackagePath | ConvertFrom-Json
$ScriptNames = @($Package.scripts.PSObject.Properties.Name)

function Resolve-LocalPython {
    $windowsPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
    if (Test-Path -LiteralPath $windowsPython) {
        return $windowsPython
    }

    $posixPython = Join-Path $RepoRoot ".venv/bin/python"
    if (Test-Path -LiteralPath $posixPython) {
        return $posixPython
    }

    return "python"
}

function Invoke-QualityScript {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [Parameter(Mandatory = $true)][string[]]$Command,
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
    $executable = $Command[0]
    $arguments = @()
    if ($Command.Count -gt 1) {
        $arguments = $Command[1..($Command.Count - 1)]
    }
    $global:LASTEXITCODE = 0
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $executable @arguments
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Quality step failed: $Name ($Script), exit code $exitCode"
    }
}

Set-Location -LiteralPath $RepoRoot
if (-not $env:PYTHONFAULTHANDLER) {
    $env:PYTHONFAULTHANDLER = "1"
}
$PythonCommand = Resolve-LocalPython

Invoke-QualityScript -Name "Productive truth scanner" -Script "quality:productive-truth" -Command @($PythonCommand, "scripts/productive-truth-scan.py")
Invoke-QualityScript -Name "Python tests" -Script "test:py" -Command @($PythonCommand, "-m", "pytest", "tests_py", "-q")
Invoke-QualityScript -Name "Web tests" -Script "test:web" -Command @("node", "scripts/run-web-tests.mjs")
Invoke-QualityScript -Name "Frontend build" -Script "build:control-center" -Command @("corepack", "pnpm@10.24.0", "exec", "vite", "build", "--config", "local-control-center/web/vite.config.ts")
Invoke-QualityScript -Name "Web typecheck" -Script "typecheck:web" -Command @("corepack", "pnpm@10.24.0", "exec", "tsc", "--noEmit", "-p", "local-control-center/web/tsconfig.json") -Optional
Invoke-QualityScript -Name "Lint" -Script "lint" -Command @("uv", "run", "--extra", "dev", "ruff", "check", ".") -Optional
Invoke-QualityScript -Name "Python format check" -Script "format:py:check" -Command @("uv", "run", "--extra", "dev", "ruff", "format", "--check", ".") -Optional
Invoke-QualityScript -Name "Web lint and format (Biome)" -Script "check:web" -Command @("corepack", "pnpm@10.24.0", "exec", "biome", "check", "local-control-center/web") -Optional
Invoke-QualityScript -Name "Architecture guardrails" -Script "quality:architecture" -Command @($PythonCommand, "-m", "pytest", "tests_py/test_real_readiness_architecture.py", "tests_py/test_internal_mock_product_boundary.py", "tests_py/test_execution_boundary_architecture.py", "tests_py/test_vertical_slices_architecture.py", "tests_py/test_web_rework_architecture.py", "-q")
Invoke-QualityScript -Name "Secret scan" -Script "security:secrets" -Command @("gitleaks", "detect", "--no-git", "--source", ".", "--config", ".gitleaks.toml", "--redact")
Invoke-QualityScript -Name "SAST" -Script "security:sast" -Command @("uv", "run", "--extra", "dev", "semgrep", "scan", "--config", ".semgrep.yml", "--no-git-ignore", "local_control_center", "tests_py", "local-control-center/web/src", "tests_web")

Write-Host "Local quality gate completed."
