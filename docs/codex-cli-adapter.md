# Codex CLI Adapter

## What It Does

`CodexCliRuntime` detects and builds safe non-interactive Codex CLI commands for workspace-bound coding runtime sessions.

## Configuration

- `CODEX_CLI_PATH=codex`
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Profiles seeded in the catalog include `codex_gpt55_developer`, `codex_gpt55_reviewer` and `codex_gpt55_xhigh_architect`.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/codex_cli/detect`
- `POST /api/v1/model-gateway/cli-runtimes/codex_cli/health-check`

## Testing

Tests verify missing binary status and dangerous flag rejection.

## Risks

- Real execution is intentionally disabled by default.
- The adapter must not modify global user Codex configuration.

## Limitations

- Usage parsing is best-effort and only consumes parseable JSON/JSONL events. Supported shapes include `usage`, `token_usage`, `tokens`, `message.usage`, `metrics.token_usage` and `llm_metrics`; free-text output still records no fabricated usage.

## Example

Generated commands include workspace-write sandbox and on-request approvals.
