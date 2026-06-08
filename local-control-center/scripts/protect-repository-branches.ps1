param(
    [string]$Owner = "",
    [string]$Repo = "",
    [string[]]$IncludeRefs = @("refs/heads/*"),
    [string[]]$ExcludeRefs = @("refs/heads/dev"),
    [string]$RulesetName = "AIDO protected branch guard",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-OriginRepositorySlug {
    $remote = git remote get-url origin 2>$null
    if (-not $remote) {
        throw "No origin remote configured. Pass -Owner and -Repo explicitly."
    }

    if ($remote -match "github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(?:\.git)?$") {
        return [pscustomobject]@{
            Owner = $Matches.owner
            Repo = $Matches.repo
        }
    }

    throw "Origin remote is not a GitHub repository URL: $remote"
}

function Assert-GhSuccess {
    param(
        [int]$ExitCode,
        [object[]]$Output,
        [string]$Context
    )

    if ($ExitCode -ne 0) {
        $message = ($Output | Out-String).Trim()
        throw "$Context failed: $message"
    }
}

function Invoke-GhApiJson {
    param(
        [string[]]$Arguments,
        [string]$InputJson = ""
    )

    $tempFile = $null
    if ($InputJson) {
        $tempFile = New-TemporaryFile
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllText($tempFile.FullName, $InputJson, $utf8NoBom)
        $apiArguments = @($Arguments + @("--input", $tempFile.FullName))
        $output = gh api @apiArguments 2>&1
    } else {
        $output = gh api @Arguments 2>&1
    }
    if ($tempFile) {
        Remove-Item -LiteralPath $tempFile.FullName -Force -ErrorAction SilentlyContinue
    }
    Assert-GhSuccess -ExitCode $LASTEXITCODE -Output $output -Context "gh api $($Arguments -join ' ')"
    return ($output | Out-String)
}

if (-not $Owner -or -not $Repo) {
    $slug = Get-OriginRepositorySlug
    if (-not $Owner) {
        $Owner = $slug.Owner
    }
    if (-not $Repo) {
        $Repo = $slug.Repo
    }
}

$rulesetsEndpoint = "/repos/$Owner/$Repo/rulesets"
$body = [ordered]@{
    name = $RulesetName
    target = "branch"
    enforcement = "active"
    conditions = [ordered]@{
        ref_name = [ordered]@{
            include = @($IncludeRefs)
            exclude = @($ExcludeRefs)
        }
    }
    rules = @(
        [ordered]@{ "type" = "creation" },
        [ordered]@{ "type" = "update" },
        [ordered]@{ "type" = "deletion" },
        [ordered]@{ "type" = "non_fast_forward" },
        [ordered]@{ "type" = "required_linear_history" },
        [ordered]@{
            "type" = "pull_request"
            parameters = [ordered]@{
                required_approving_review_count = 1
                dismiss_stale_reviews_on_push = $true
                require_code_owner_review = $true
                require_last_push_approval = $true
                required_review_thread_resolution = $true
            }
        }
    )
    bypass_actors = @()
}

$json = $body | ConvertTo-Json -Depth 20

if ($DryRun) {
    [pscustomobject]@{
        endpoint = $rulesetsEndpoint
        include = $IncludeRefs
        exclude = $ExcludeRefs
        body = $body
    } | ConvertTo-Json -Depth 20
    exit 0
}

gh auth status | Out-Null
Assert-GhSuccess -ExitCode $LASTEXITCODE -Output @() -Context "gh auth status"

$existingRaw = Invoke-GhApiJson -Arguments @($rulesetsEndpoint)
$existing = $existingRaw | ConvertFrom-Json
$matchingRuleset = @($existing | Where-Object { $_.name -eq $RulesetName } | Select-Object -First 1)

if ($matchingRuleset.Count -gt 0) {
    $rulesetId = $matchingRuleset[0].id
    $endpoint = "/repos/$Owner/$Repo/rulesets/$rulesetId"
    Invoke-GhApiJson -Arguments @(
        "--method", "PUT",
        "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2022-11-28",
        $endpoint
    ) -InputJson $json | Out-Null
    Write-Host "Updated GitHub ruleset '$RulesetName' in $Owner/$Repo."
} else {
    Invoke-GhApiJson -Arguments @(
        "--method", "POST",
        "-H", "Accept: application/vnd.github+json",
        "-H", "X-GitHub-Api-Version: 2022-11-28",
        $rulesetsEndpoint
    ) -InputJson $json | Out-Null
    Write-Host "Created GitHub ruleset '$RulesetName' in $Owner/$Repo."
}
