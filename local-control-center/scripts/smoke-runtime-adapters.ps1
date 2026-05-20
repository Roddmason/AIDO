param(
    [string]$BaseUrl = "http://127.0.0.1:4310",
    [string]$Workspace = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:AIDO_RUNTIME_SMOKE -ne "1") {
    Write-Host "Skip optional runtime adapter smoke. Set AIDO_RUNTIME_SMOKE=1 to run."
    exit 0
}
if ($env:AIDO_RUNTIME_RELEASE_VALIDATION -eq "1") {
    # Release validation requires AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON and AIDO_OPENHANDS_ISSUE_TEXT.
    # Release validation requires AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON and AIDO_SWE_AGENT_ISSUE_TEXT.
    $env:AIDO_RUNTIME_ISSUE_TO_PATCH_SMOKE = "1"
}

function Invoke-AidoJson {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [object]$Body = $null,
        [string]$Token = ""
    )
    $headers = @{ Accept = "application/json" }
    if ($Token) {
        $headers["X-Local-Control-Token"] = $Token
    }
    $uri = "$BaseUrl$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers
    }
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 20)
}

function New-AgentProfile {
    param(
        [string]$Id,
        [string]$RuntimeMode,
        [string[]]$AllowedTools
    )
    Invoke-AidoJson -Method "POST" -Path "/api/v1/agent-profiles" -Token $token -Body @{
        id = $Id
        name = $Id
        role = "implementer"
        runtimeMode = $RuntimeMode
        modelPolicyId = "implementation_default"
        permissionProfile = "dev_safe"
        allowedTools = $AllowedTools
    } | Out-Null
}

function New-AgentRun {
    param(
        [string]$AgentProfileId,
        [string]$TaskId,
        [object[]]$ToolCalls
    )
    Invoke-AidoJson -Method "POST" -Path "/api/v1/agent-runs" -Token $token -Body @{
        projectId = $projectId
        agentProfileId = $AgentProfileId
        taskId = $TaskId
        input = @{ toolCalls = $ToolCalls }
    }
}

function Resolve-OptionalCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Names
    )
    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            return $command.Source
        }
    }
    return ""
}

function Read-ArgvJsonEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value) {
        return $null
    }
    $argv = @($value | ConvertFrom-Json)
    if ($argv.Count -lt 1) {
        throw "$Name must be a non-empty JSON array."
    }
    return $argv
}

function Assert-ReleaseIssueToPatchConfigured {
    param(
        [Parameter(Mandatory = $true)][string]$AdapterName,
        [Parameter(Mandatory = $true)][string]$ArgvEnvName,
        [Parameter(Mandatory = $true)][string]$IssueEnvName
    )
    if ($env:AIDO_RUNTIME_RELEASE_VALIDATION -ne "1") {
        return
    }
    $argv = Read-ArgvJsonEnv -Name $ArgvEnvName
    if (-not $argv) {
        throw "Release validation requires $ArgvEnvName for $AdapterName issue_to_patch smoke."
    }
    if (-not [Environment]::GetEnvironmentVariable($IssueEnvName)) {
        throw "Release validation requires $IssueEnvName for $AdapterName issue_to_patch smoke."
    }
    $executable = [string]$argv[0]
    if (-not (Test-Path -LiteralPath $executable) -and -not (Get-Command $executable -ErrorAction SilentlyContinue)) {
        throw "Release validation runtime command not found for $AdapterName issue_to_patch smoke: $executable"
    }
}

Invoke-AidoJson -Method "GET" -Path "/healthz" | Out-Null
$token = (Invoke-AidoJson -Method "GET" -Path "/api/v1/security/handshake").token
$projects = Invoke-AidoJson -Method "GET" -Path "/api/v1/projects"
if (-not $projects.projects -or $projects.projects.Count -lt 1) {
    throw "No project is available. Start the control center so the runtime project is initialized."
}
$projectId = $projects.projects[0].id
$projectPath = $projects.projects[0].path
if (-not $projectPath) {
    $projectPath = $Workspace
}
$suffix = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()

New-AgentProfile -Id "smoke_internal_$suffix" -RuntimeMode "internal_mock" -AllowedTools @("policy.evaluate")
Invoke-AidoJson -Method "POST" -Path "/api/v1/agent-runs" -Token $token -Body @{
    projectId = $projectId
    agentProfileId = "smoke_internal_$suffix"
    taskId = "internal_mock_smoke"
    input = @{ goal = "internal mock runtime smoke" }
} | Out-Null

if ($env:AIDO_MCP_SMOKE_COMMAND) {
    Invoke-AidoJson -Method "POST" -Path "/api/v1/integrations/mcp/register" -Token $token -Body @{
        id = "mcp_smoke_$suffix"
        command = $env:AIDO_MCP_SMOKE_COMMAND
        transport = "stdio"
        metadata = @{ source = "smoke-runtime-adapters.ps1" }
    } | Out-Null
    New-AgentProfile -Id "smoke_mcp_$suffix" -RuntimeMode "hybrid" -AllowedTools @("mcp")
    New-AgentRun -AgentProfileId "smoke_mcp_$suffix" -TaskId "mcp_smoke" -ToolCalls @(@{
        tool = "mcp"
        operation = "tools/list"
        serverId = "mcp_smoke_$suffix"
        workspacePath = $projectPath
        path = $projectPath
        execute = $true
        timeoutSeconds = 20
    }) | Out-Null
}

$argv = $null
if ($env:AIDO_OPENHANDS_SMOKE_ARGV_JSON) {
    $argv = @($env:AIDO_OPENHANDS_SMOKE_ARGV_JSON | ConvertFrom-Json)
} else {
    $openhandsCommand = Resolve-OptionalCommand -Names @("openhands", "openhands.exe")
    if ($openhandsCommand) {
        Write-Host "Detected OpenHands CLI; running openhands --version through broker/policy."
        $argv = @($openhandsCommand, "--version")
    } else {
        Write-Host "Skip OpenHands version smoke. OpenHands CLI was not found and AIDO_OPENHANDS_SMOKE_ARGV_JSON is not set."
    }
}
if ($argv) {
    New-AgentProfile -Id "smoke_openhands_$suffix" -RuntimeMode "hybrid" -AllowedTools @("openhands")
    New-AgentRun -AgentProfileId "smoke_openhands_$suffix" -TaskId "openhands_smoke" -ToolCalls @(@{
        tool = "openhands"
        argv = $argv
        workspacePath = $projectPath
        path = $projectPath
        execute = $true
        timeoutSeconds = 60
    }) | Out-Null
}

if ($env:AIDO_RUNTIME_ISSUE_TO_PATCH_SMOKE -eq "1") {
    Assert-ReleaseIssueToPatchConfigured `
        -AdapterName "OpenHands" `
        -ArgvEnvName "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON" `
        -IssueEnvName "AIDO_OPENHANDS_ISSUE_TEXT"
    $issueArgv = $null
    if ($env:AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON) {
        $issueArgv = Read-ArgvJsonEnv -Name "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON"
    }
    if ($issueArgv) {
        $issueText = $env:AIDO_OPENHANDS_ISSUE_TEXT
        if (-not $issueText) {
            $issueText = "Create a minimal no-op validation patch only if required by the release smoke profile."
        }
        New-AgentProfile -Id "smoke_openhands_issue_$suffix" -RuntimeMode "hybrid" -AllowedTools @("openhands")
        New-AgentRun -AgentProfileId "smoke_openhands_issue_$suffix" -TaskId "openhands_issue_to_patch_smoke" -ToolCalls @(@{
            tool = "openhands"
            operation = "issue_to_patch"
            argv = $issueArgv
            issueText = $issueText
            workspacePath = $projectPath
            path = $projectPath
            execute = $true
            timeoutSeconds = 900
        }) | Out-Null
    } else {
        Write-Host "Skip OpenHands issue_to_patch smoke. Set AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON for the installed CLI syntax."
    }
}

$argv = $null
if ($env:AIDO_SWE_AGENT_SMOKE_ARGV_JSON) {
    $argv = @($env:AIDO_SWE_AGENT_SMOKE_ARGV_JSON | ConvertFrom-Json)
} else {
    $sweAgentCommand = Resolve-OptionalCommand -Names @("swe-agent", "swe-agent.exe", "sweagent", "sweagent.exe")
    if ($sweAgentCommand) {
        Write-Host "Detected SWE-agent CLI; running swe-agent --version through broker/policy."
        $argv = @($sweAgentCommand, "--version")
    } else {
        Write-Host "Skip SWE-agent version smoke. SWE-agent CLI was not found and AIDO_SWE_AGENT_SMOKE_ARGV_JSON is not set."
    }
}
if ($argv) {
    New-AgentProfile -Id "smoke_swe_agent_$suffix" -RuntimeMode "hybrid" -AllowedTools @("swe_agent")
    New-AgentRun -AgentProfileId "smoke_swe_agent_$suffix" -TaskId "swe_agent_smoke" -ToolCalls @(@{
        tool = "swe_agent"
        argv = $argv
        workspacePath = $projectPath
        path = $projectPath
        execute = $true
        timeoutSeconds = 60
    }) | Out-Null
}

if ($env:AIDO_RUNTIME_ISSUE_TO_PATCH_SMOKE -eq "1") {
    Assert-ReleaseIssueToPatchConfigured `
        -AdapterName "SWE-agent" `
        -ArgvEnvName "AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON" `
        -IssueEnvName "AIDO_SWE_AGENT_ISSUE_TEXT"
    $issueArgv = $null
    if ($env:AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON) {
        $issueArgv = Read-ArgvJsonEnv -Name "AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON"
    }
    if ($issueArgv) {
        $issueText = $env:AIDO_SWE_AGENT_ISSUE_TEXT
        if (-not $issueText) {
            $issueText = "Create a minimal no-op validation patch only if required by the release smoke profile."
        }
        New-AgentProfile -Id "smoke_swe_agent_issue_$suffix" -RuntimeMode "hybrid" -AllowedTools @("swe_agent")
        New-AgentRun -AgentProfileId "smoke_swe_agent_issue_$suffix" -TaskId "swe_agent_issue_to_patch_smoke" -ToolCalls @(@{
            tool = "swe_agent"
            operation = "issue_to_patch"
            argv = $issueArgv
            issueText = $issueText
            workspacePath = $projectPath
            path = $projectPath
            execute = $true
            timeoutSeconds = 900
        }) | Out-Null
    } else {
        Write-Host "Skip SWE-agent issue_to_patch smoke. Set AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON for the installed CLI syntax."
    }
}

Write-Host "Runtime adapter smoke completed. Optional adapters skipped unless their AIDO_*_SMOKE variables were set."
exit 0
