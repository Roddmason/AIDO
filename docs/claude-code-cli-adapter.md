# Claude Code CLI Adapter

## What It Does

`ClaudeCodeCliRuntime` detects Claude Code CLI and builds workspace-bound commands for optional coding-runtime use.

## Configuration

- `CLAUDE_CODE_CLI_PATH=claude`
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Seeded profiles include Sonnet for normal development/QA and Opus only for planning, difficult debugging or architecture escalation.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/claude_code_cli/detect`
- `POST /api/v1/model-gateway/cli-runtimes/claude_code_cli/health-check`

## Testing

Tests verify missing binary status returns `Claude Code CLI not detected`.

## Risks

- Opus/max effort is not default and requires budget/approval policy.
- Real execution must remain policy-approved and workspace-bound.

## Limitations

- Usage is estimated unless the CLI exposes exact usage in captured output.

## Example

`claude_sonnet_developer` maps to a Sonnet-class model with medium effort.
