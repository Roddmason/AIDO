param(
    [string]$OutputRoot = "",
    [switch]$FailOnSkippedSmokes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot ".tmp\release-certification\$Timestamp"
} else {
    $OutputRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputRoot)
}

$ReportDir = Join-Path $OutputRoot "reports"
$LogDir = Join-Path $OutputRoot "logs"
$ReportPath = Join-Path $ReportDir "release-certification-report.json"
$QualityLogPath = Join-Path $LogDir "quality.log"

$script:SecretMasks = New-Object System.Collections.Generic.List[string]

function Register-SecretMask {
    param([string]$Value)
    if (-not $Value) {
        return
    }
    if ($Value.Length -lt 4) {
        return
    }
    if (-not $script:SecretMasks.Contains($Value)) {
        $script:SecretMasks.Add($Value) | Out-Null
    }
}

function Initialize-SecretMasks {
    $secretKeywords = @("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY", "AUTHORIZATION")
    foreach ($item in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $name = [string]$item.Key
        if ($secretKeywords | Where-Object { $name -match $_ }) {
            Register-SecretMask -Value ([string]$item.Value)
        }
    }
}

function Redact-SecretText {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) {
        return ""
    }

    $redacted = $Text
    foreach ($mask in $script:SecretMasks) {
        if ($mask) {
            $redacted = [regex]::Replace($redacted, [regex]::Escape($mask), "[redacted]")
        }
    }

    $patterns = @(
        "Bearer\s+[A-Za-z0-9._-]+",
        "ghp_[A-Za-z0-9_]{12,}",
        "github_pat_[A-Za-z0-9_]{20,}",
        "glpat-[A-Za-z0-9_-]{12,}",
        "xox[baprs]-[A-Za-z0-9-]{10,}",
        "AKIA[0-9A-Z]{16}",
        "sk-[A-Za-z0-9_-]{8,}",
        "password\s*=\s*[^&\s]+"
    )
    foreach ($pattern in $patterns) {
        $redacted = [regex]::Replace($redacted, $pattern, "[redacted]", [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    }
    return $redacted
}

function ConvertTo-RedactedJson {
    param([Parameter(Mandatory = $true)][object]$Value)
    return Redact-SecretText -Text ($Value | ConvertTo-Json -Depth 40)
}

function ConvertTo-CmdArgument {
    param([AllowNull()][string]$Argument)
    if ($null -eq $Argument) {
        return '""'
    }
    if ($Argument.Length -eq 0) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }
    return '"' + ($Argument -replace '"', '\"') + '"'
}

function Join-CmdCommandLine {
    param([Parameter(Mandatory = $true)][string[]]$CommandAndArguments)
    return (($CommandAndArguments | ForEach-Object { ConvertTo-CmdArgument -Argument ([string]$_) }) -join " ")
}

function Write-RedactedJsonFile {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    ConvertTo-RedactedJson -Value $Value | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Write-ReleaseReport {
    param([Parameter(Mandatory = $true)][object]$Report)
    Write-RedactedJsonFile -Value $Report -Path $ReportPath
}

function Invoke-ReleaseCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [string]$PackageScript = "",
        [string[]]$Command = @(),
        [string[]]$Arguments = @(),
        [string]$ReportPath = ""
    )

    $startedAt = [DateTimeOffset]::UtcNow.ToString("o")
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
    Write-Host (Redact-SecretText -Text "==> $Name")

    if ($Command.Count -gt 0) {
        $executable = $Command[0]
        $argv = @()
        if ($Command.Count -gt 1) {
            $argv = $Command[1..($Command.Count - 1)]
        }
        $argv = $argv + $Arguments
    } else {
        $executable = "corepack"
        $argv = @("pnpm@10.24.0", "run", $PackageScript) + $Arguments
    }
    $output = @()
    $exitCode = 1
    $commandFile = Join-Path (Split-Path -Parent $LogPath) ("invoke-" + ([guid]::NewGuid().ToString("N")) + ".cmd")
    try {
        $commandLine = Join-CmdCommandLine -CommandAndArguments (@($executable) + $argv)
        @(
            "@echo off"
            "cd /d " + (ConvertTo-CmdArgument -Argument $RepoRoot)
            $commandLine
            "exit /b %ERRORLEVEL%"
        ) | Set-Content -LiteralPath $commandFile -Encoding ASCII

        $processInfo = New-Object System.Diagnostics.ProcessStartInfo
        $processInfo.FileName = "cmd.exe"
        $processInfo.Arguments = '/d /s /c ""' + $commandFile + '""'
        $processInfo.WorkingDirectory = $RepoRoot
        $processInfo.UseShellExecute = $false
        $processInfo.RedirectStandardOutput = $true
        $processInfo.RedirectStandardError = $true
        $processInfo.CreateNoWindow = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $processInfo
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        if ($stdout) {
            $output += "## stdout"
            $output += ($stdout -split "\r?\n")
        }
        if ($stderr) {
            $output += "## stderr"
            $output += ($stderr -split "\r?\n")
        }
    } catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    } finally {
        if (Test-Path -LiteralPath $commandFile) {
            Remove-Item -LiteralPath $commandFile -Force -ErrorAction SilentlyContinue
        }
    }

    $failureReason = ""
    if ($ReportPath -and -not (Test-Path -LiteralPath $ReportPath)) {
        if ($exitCode -eq 0) {
            $exitCode = 1
            $failureReason = "release_smoke_report_missing"
        } else {
            $failureReason = "release_smoke_failed"
        }
        $output += "Required report artifact was not created: $ReportPath"
        Write-RedactedJsonFile -Value ([pscustomobject]@{
            name = $Name
            packageScript = $PackageScript
            status = "failed"
            reason = $failureReason
            reportPath = $ReportPath
        }) -Path $ReportPath
    }

    $redactedOutput = @($output | ForEach-Object { Redact-SecretText -Text ([string]$_) })
    $redactedOutput | Set-Content -LiteralPath $LogPath -Encoding UTF8

    if ($exitCode -eq 0) {
        $status = "passed"
    } else {
        $status = "failed"
    }
    $result = [pscustomobject]@{
        name = $Name
        packageScript = $PackageScript
        status = $status
        execution = "executed"
        exitCode = $exitCode
        startedAt = $startedAt
        finishedAt = [DateTimeOffset]::UtcNow.ToString("o")
        logPath = $LogPath
        arguments = @($Arguments)
    }
    if ($ReportPath) {
        $result | Add-Member -NotePropertyName reportPath -NotePropertyValue $ReportPath
    }
    if ($failureReason) {
        $result | Add-Member -NotePropertyName reason -NotePropertyValue $failureReason
    }
    return $result
}

function Test-EnvEquals {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    return ([Environment]::GetEnvironmentVariable($Name) -eq $Expected)
}

function Test-EnvPresent {
    param([Parameter(Mandatory = $true)][string]$Name)
    return -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name))
}

function Get-MissingSmokeConfiguration {
    param([Parameter(Mandatory = $true)][object]$Smoke)

    $missing = @()
    foreach ($envName in $Smoke.requiredEnv) {
        if (-not (Test-EnvPresent -Name $envName)) {
            $missing += "$envName is required."
        }
    }
    foreach ($flag in $Smoke.requiredFlags.GetEnumerator()) {
        if (-not (Test-EnvEquals -Name ([string]$flag.Key) -Expected ([string]$flag.Value))) {
            $missing += "$($flag.Key)=$($flag.Value) is required."
        }
    }
    return $missing
}

Initialize-SecretMasks
New-Item -ItemType Directory -Force -Path $ReportDir, $LogDir | Out-Null
Set-Location -LiteralPath $RepoRoot

$smokes = @(
    [pscustomobject]@{
        name = "Codex CLI release smoke"
        packageScript = "smoke:codex:release"
        reportFile = "codex-cli-release-report.json"
        logFile = "codex-cli-release.log"
        requiredEnv = @("AIDO_CODEX_COMMAND")
        requiredFlags = @{ AIDO_ENABLE_CLI_RUNTIMES = "true" }
    },
    [pscustomobject]@{
        name = "Claude Code CLI release smoke"
        packageScript = "smoke:claude:release"
        reportFile = "claude-code-cli-release-report.json"
        logFile = "claude-code-cli-release.log"
        requiredEnv = @("AIDO_CLAUDE_COMMAND")
        requiredFlags = @{ AIDO_ENABLE_CLI_RUNTIMES = "true" }
    },
    [pscustomobject]@{
        name = "OpenHands release smoke"
        packageScript = "smoke:openhands:release"
        reportFile = "openhands-release-report.json"
        logFile = "openhands-release.log"
        requiredEnv = @("AIDO_OPENHANDS_COMMAND")
        requiredFlags = @{ AIDO_ENABLE_CLI_RUNTIMES = "true" }
    },
    [pscustomobject]@{
        name = "SWE-agent release smoke"
        packageScript = "smoke:swe-agent:release"
        reportFile = "swe-agent-release-report.json"
        logFile = "swe-agent-release.log"
        requiredEnv = @("AIDO_SWE_AGENT_COMMAND")
        requiredFlags = @{ AIDO_ENABLE_CLI_RUNTIMES = "true" }
    }
)

$startedAt = [DateTimeOffset]::UtcNow.ToString("o")
$qualityCommand = @("corepack", "pnpm@10.24.0", "run", "quality")
$quality = Invoke-ReleaseCommand -Name "quality" -Command $qualityCommand -PackageScript "quality" -LogPath $QualityLogPath
$smokeResults = @()

if ($quality.exitCode -eq 0) {
    foreach ($smoke in $smokes) {
        $missing = @(Get-MissingSmokeConfiguration -Smoke $smoke)
        $childReportPath = Join-Path $ReportDir $smoke.reportFile
        $childLogPath = Join-Path $LogDir $smoke.logFile
        if ($missing.Count -gt 0) {
            $skippedSmoke = [pscustomobject]@{
                name = $smoke.name
                packageScript = $smoke.packageScript
                status = "configuration_required"
                execution = "skipped"
                reason = "Missing required release-smoke environment variables."
                errors = $missing
                reportPath = $childReportPath
                logPath = $childLogPath
            }
            Write-RedactedJsonFile -Value $skippedSmoke -Path $childReportPath
            @(
                "status=configuration_required"
                "execution=skipped"
                "reason=Missing required release-smoke environment variables."
                "errors:"
                $missing
            ) | ForEach-Object { Redact-SecretText -Text ([string]$_) } | Set-Content -LiteralPath $childLogPath -Encoding UTF8
            $smokeResults += $skippedSmoke
            continue
        }

        $smokeResults += Invoke-ReleaseCommand `
            -Name $smoke.name `
            -PackageScript $smoke.packageScript `
            -LogPath $childLogPath `
            -ReportPath $childReportPath `
            -Arguments @("--", "--report-path", $childReportPath)
    }
}

$failedSmokes = @($smokeResults | Where-Object { $_.status -eq "failed" })
$configurationRequiredSmokes = @($smokeResults | Where-Object { $_.status -eq "configuration_required" })
$overallStatus = "passed"
$overallReason = "passed"
if ($quality.exitCode -ne 0) {
    $overallStatus = "failed"
    $overallReason = "quality_failed"
} elseif ($failedSmokes.Count -gt 0) {
    $overallStatus = "failed"
    $overallReason = "release_smoke_failed"
} elseif ($FailOnSkippedSmokes -and $configurationRequiredSmokes.Count -gt 0) {
    $overallStatus = "failed"
    $overallReason = "configuration_required"
}

if ($quality.exitCode -ne 0) {
    $certificationScope = "quality_failed"
} elseif ($failedSmokes.Count -gt 0) {
    $certificationScope = "quality_with_failed_release_smokes"
} elseif ($configurationRequiredSmokes.Count -gt 0) {
    $certificationScope = "quality_with_configuration_required_smokes"
} else {
    $certificationScope = "quality_and_release_smokes"
}

$artifacts = @(
    [pscustomobject]@{ kind = "release_report"; path = $ReportPath },
    [pscustomobject]@{ kind = "quality_log"; path = $QualityLogPath }
)
foreach ($smokeResult in $smokeResults) {
    if ($smokeResult.PSObject.Properties.Name -contains "reportPath") {
        $artifacts += [pscustomobject]@{ kind = "release_smoke_report"; path = $smokeResult.reportPath }
    }
    if ($smokeResult.PSObject.Properties.Name -contains "logPath") {
        $artifacts += [pscustomobject]@{ kind = "release_smoke_log"; path = $smokeResult.logPath }
    }
}

$report = [pscustomobject]@{
    schema = "aido.release-certification.v1"
    status = $overallStatus
    reason = $overallReason
    certificationScope = $certificationScope
    startedAt = $startedAt
    finishedAt = [DateTimeOffset]::UtcNow.ToString("o")
    repoRoot = $RepoRoot
    outputRoot = $OutputRoot
    reportsPath = $ReportDir
    logsPath = $LogDir
    reportPath = $ReportPath
    strictReleaseSmokes = [bool]$FailOnSkippedSmokes
    quality = $quality
    releaseSmokes = $smokeResults
    artifacts = $artifacts
}

Write-ReleaseReport -Report $report
Write-Host (Redact-SecretText -Text "Release certification report: $ReportPath")

if ($overallStatus -ne "passed") {
    exit 1
}
exit 0
