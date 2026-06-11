# CLI Runtimes

## What It Does

CLI runtimes are coding/runtime adapters, not model providers. The gateway separates `codex_cli`, `claude_code_cli`, `openhands`, `swe_agent` and `manual` from API model calls.

## Configuration

- `AIDO_CODEX_COMMAND=codex`
- `AIDO_CLAUDE_COMMAND=claude`
- `AIDO_OPENHANDS_COMMAND=openhands`
- `AIDO_SWE_AGENT_COMMAND=sweagent`
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
$env:AIDO_CODEX_COMMAND = "codex"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:codex:release
```

Claude Code has the equivalent release smoke:

```powershell
$env:AIDO_CLAUDE_COMMAND = "claude"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:claude:release
```

These commands use temporary Git repositories and the real `issue_to_patch`
runner. They must fail if the CLI is not installed, the configured command or
CLI syntax fails, the execution is not workspace-bound, the patch is empty, QA
does not pass, or evidence artifacts and SHA-256 hashes are incomplete. A CLI
provider may be `available` after detection/version checks, but it is not
`executable` for productive workflow use unless it advertises the
`issue_to_patch` capability.

OpenHands and SWE-agent do not receive `issue_to_patch` from the seed data. A
detected binary proves only version-check availability, not workspace editing
compatibility. Their release smokes insert `issue_to_patch` only in the
temporary validation database used by the smoke run.

Supported default syntax:

```text
openhands --headless --json -t "<issue_to_patch prompt>"
sweagent run --env.repo.path=<workspace> --problem_statement.text="<issue_to_patch prompt>" --actions.apply_patch_locally
```

The process working directory is the allocated Git worktree. The old
`openhands run --workspace` and `sweagent run --repo` forms are not part of the
AIDO contract.

If an installed CLI needs a different supported syntax, provide structured argv
JSON through:

```powershell
$env:AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON = '["openhands","--headless","--json","-t","{prompt}"]'
$env:AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON = '["sweagent","run","--env.repo.path={workspace}","--problem_statement.text={prompt}","--actions.apply_patch_locally"]'
```

Supported placeholders are `{workspace}`, `{workspace_path}`, `{prompt}`,
`{issue_text}` and `{title}`. Invalid JSON blocks execution; the runtime does
not silently fall back to another command.

Run the optional release smokes with:

```powershell
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
