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

if ($env:AIDO_OPENHANDS_SMOKE_ARGV_JSON) {
    $argv = @($env:AIDO_OPENHANDS_SMOKE_ARGV_JSON | ConvertFrom-Json)
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

if ($env:AIDO_SWE_AGENT_SMOKE_ARGV_JSON) {
    $argv = @($env:AIDO_SWE_AGENT_SMOKE_ARGV_JSON | ConvertFrom-Json)
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

Write-Host "Runtime adapter smoke completed. Optional adapters skipped unless their AIDO_*_SMOKE variables were set."
exit 0
