# CLI Runtimes

## What It Does

CLI runtimes are coding/runtime adapters, not model providers. The gateway separates `codex_cli`, `claude_code_cli`, `openhands`, `swe_agent` and `manual` from API model calls.

## Configuration

- `runtime_installations` stores executable path, detected version, capabilities,
  preferred roles, health, last validation, and configuration source.
- `runtime_accounts` stores account label, auth mode, enabled/default state,
  capabilities, preferred roles, health, last validation, and configuration
  source.
- Codex and Claude Code seed provider-native CLI accounts
  (`authMode=provider_native_cli`), so AIDO uses each CLI's native login/session
  by default instead of copying secrets.
- Command env vars such as `AIDO_CODEX_COMMAND`, `AIDO_CLAUDE_COMMAND`,
  `AIDO_OPENHANDS_COMMAND`, and `AIDO_SWE_AGENT_COMMAND` are deprecated
  bootstrap/CI overrides only.
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Execution is disabled by default. Detection and health checks are safe.
Blocked, failed and real runtime attempts persist `cli_sessions` rows with
redacted command/env policy, linked `usage_ledger` records and evidence
artifacts when stdout, stderr or structured logs exist.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/detect`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/health-check`
- `GET /api/v1/model-gateway/cli-sessions`
- `GET /api/v1/model-gateway/cli-sessions/{id}`

## Testing

Tests verify missing Codex/Claude binaries return `not_installed`, dangerous
flags are blocked, generic JSON/JSONL usage events and runtime-specific aliases
are parsed, tests isolate subprocess execution with monkeypatches, blocked paths
still create sessions, and sessions list as JSON. Product runtime classes do not
expose a mock execution mode.

Codex has an additional real release smoke that is intentionally opt-in and
excluded from unit tests:

```powershell
# Deprecated CI/bootstrap override; normal runtime config lives in SQLite.
$env:AIDO_CODEX_COMMAND = "codex"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:codex:release
```

Claude Code has the equivalent release smoke:

```powershell
# Deprecated CI/bootstrap override; normal runtime config lives in SQLite.
$env:AIDO_CLAUDE_COMMAND = "claude"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:claude:release
```

These commands use temporary Git repositories and the real `issue_to_patch`
workflow. Productive implementation still runs through `DeveloperAgentRunner`.
They must fail if the CLI is not installed, the configured command or CLI syntax
fails, the execution is not workspace-bound, the patch is empty, QA does not
pass, or evidence artifacts and SHA-256 hashes are incomplete. A CLI provider
may be `available` after detection/version checks, but it is not `executable`
for DeveloperAgent workflow use unless it advertises the `code_edit`
capability.

OpenHands and SWE-agent do not receive `code_edit` from the seed data. A
detected binary proves only version-check availability, not DeveloperAgent
workspace editing compatibility. Their release smokes are optional adapter
validations and do not make them canonical implementation runtimes.

Supported default syntax:

```text
openhands --headless --json -t "<issue_to_patch prompt>"
sweagent run --env.repo.path=<workspace> --problem_statement.text="<issue_to_patch prompt>" --actions.apply_patch_locally
```

The process working directory is the allocated Git worktree. The old
`openhands run --workspace` and `sweagent run --repo` forms are not part of the
AIDO contract.

If an installed optional adapter needs a different supported syntax, provide
structured argv JSON through:

```powershell
$env:AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON = '["openhands","--headless","--json","-t","{prompt}"]'
$env:AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON = '["sweagent","run","--env.repo.path={workspace}","--problem_statement.text={prompt}","--actions.apply_patch_locally"]'
```

Supported placeholders are `{workspace}`, `{workspace_path}`, `{prompt}`,
`{issue_text}` and `{title}`. Invalid JSON blocks execution; the runtime does
not silently fall back to another command.

Run the optional release smokes with:

```powershell
# Deprecated CI/bootstrap overrides for optional release smokes.
$env:AIDO_OPENHANDS_COMMAND = "openhands"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:openhands:release

$env:AIDO_SWE_AGENT_COMMAND = "sweagent"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:swe-agent:release
```

Each smoke creates a temporary Git repository, runs real `issue_to_patch`,
requires a non-empty patch, passing QA, evidence hashes, and linked stdout and
stderr artifacts. Missing CLI, syntax mismatch, non-Git workspace, empty patch,
or incomplete artifacts fails the release validation.

To certify a release candidate with quality plus every configured release
smoke, run:

```powershell
corepack pnpm@10.24.0 run release:certify
```

The runner writes `.tmp/release-certification/<timestamp>/reports/` and
`.tmp/release-certification/<timestamp>/logs/`, redacts secret-like output, and
passes `--report-path` to each configured smoke. It does not auto-detect a CLI
as proof of execution: Codex, Claude Code, OpenHands, and SWE-agent smokes run
only when their command env var and `AIDO_ENABLE_CLI_RUNTIMES=true` are present.
Missing optional variables are recorded as `configuration_required`. Use
`scripts/release-certify.ps1 -FailOnSkippedSmokes` for a strict release profile
where skipped optional smokes must fail the certification.

## Risks

- Real CLI execution must stay workspace-bound and policy-approved.
- Artifact content and metadata are redacted before persistence. Artifact files
  are written under the local evidence-artifact root and linked through
  `stdout_artifact_id`, `stderr_artifact_id` and `logs_artifact_id`.
- Usage parsing only trusts structured usage payloads; plain text output is not token-counted to avoid false precision.
- Supported alias shapes include `usage`, `token_usage`, `tokens`, `message.usage`, `metrics.token_usage` and `llm_metrics`.

## Limitations

- The adapters build safe command lines, detect binaries and parse generic/runtime-alias JSON or JSONL usage events. Runtime-specific schemas can still change, so unrecognized shapes remain `None` instead of guessed token counts.

## Example

Blocked flags include `--dangerously-bypass-approvals-and-sandbox`, `--yolo` and `danger-full-access`.
