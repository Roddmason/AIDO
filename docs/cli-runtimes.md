# CLI Runtimes

## What It Does

CLI runtimes are coding/runtime adapters, not model providers. The gateway separates `codex_cli`, `claude_code_cli`, `openhands`, `swe_agent` and `manual` from API model calls.

## Configuration

- `CODEX_CLI_PATH=codex`
- `CLAUDE_CODE_CLI_PATH=claude`
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Execution is disabled by default. Detection and health checks are safe.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/detect`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/health-check`
- `GET /api/v1/model-gateway/cli-sessions`

## Testing

Tests verify missing Codex/Claude binaries return `not_installed`, dangerous flags are blocked and sessions list as JSON.

## Risks

- Real CLI execution must stay workspace-bound and policy-approved.
- Output artifacts are currently planned for real execution; mock execution does not create logs.

## Limitations

- The adapters build safe command lines and detect binaries; full event-stream parsing is stubbed until real runtime contracts are finalized.

## Example

Blocked flags include `--dangerously-bypass-approvals-and-sandbox`, `--yolo` and `danger-full-access`.
