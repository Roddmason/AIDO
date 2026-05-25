# CLI Runtimes

## What It Does

CLI runtimes are coding/runtime adapters, not model providers. The gateway separates `codex_cli`, `claude_code_cli`, `openhands`, `swe_agent` and `manual` from API model calls.

## Configuration

- `CODEX_CLI_PATH=codex`
- `CLAUDE_CODE_CLI_PATH=claude`
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Execution is disabled by default. Detection and health checks are safe. Mock,
blocked, failed and real runtime attempts persist `cli_sessions` rows with
redacted command/env policy, linked `usage_ledger` records and evidence artifacts
when stdout, stderr or structured logs exist.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/detect`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/health-check`
- `GET /api/v1/model-gateway/cli-sessions`
- `GET /api/v1/model-gateway/cli-sessions/{id}`

## Testing

Tests verify missing Codex/Claude binaries return `not_installed`, dangerous
flags are blocked, generic JSON/JSONL usage events and runtime-specific aliases
are parsed, mock runtime execution persists CLI sessions, usage and artifacts,
blocked paths still create sessions, and sessions list as JSON.

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
