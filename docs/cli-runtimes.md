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

Tests verify missing Codex/Claude binaries return `not_installed`, dangerous flags are blocked, JSON/JSONL usage events are parsed and sessions list as JSON.

## Risks

- Real CLI execution must stay workspace-bound and policy-approved.
- Output artifacts are currently planned for real execution; mock execution does not create logs.
- Usage parsing only trusts structured usage payloads; plain text output is not token-counted to avoid false precision.

## Limitations

- The adapters build safe command lines, detect binaries and parse generic JSON/JSONL usage events. Runtime-specific event streams still need contract-level parsers before real execution should be treated as high-fidelity telemetry.

## Example

Blocked flags include `--dangerously-bypass-approvals-and-sandbox`, `--yolo` and `danger-full-access`.
