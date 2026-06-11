# Claude Code CLI Adapter

## What It Does

`ClaudeCodeCliRuntime` detects Claude Code CLI and builds workspace-bound commands for optional coding-runtime use.

## Configuration

- `AIDO_CLAUDE_COMMAND=claude`
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Seeded profiles include Sonnet for normal development/QA and Opus only for planning, difficult debugging or architecture escalation.

## Supported Syntax

The supported workspace-edit invocation is:

```text
claude --print --permission-mode acceptEdits --add-dir <workspace> [--model <model>] "<prompt>"
```

The process itself is still launched by AIDO with `cwd=<workspace>` through a
structured argv subprocess. `--print` makes the run non-interactive,
`--permission-mode acceptEdits` allows file edits without interactive approval,
and `--add-dir <workspace>` grants Claude Code explicit access to the allocated
workspace. `--cwd` is not part of the supported AIDO contract.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/claude_code_cli/detect`
- `POST /api/v1/model-gateway/cli-runtimes/claude_code_cli/health-check`

## Testing

Tests verify missing binary status returns `Claude Code CLI not detected`.

Release validation is a real opt-in smoke, not part of the unit suite:

```powershell
$env:AIDO_CLAUDE_COMMAND = "claude"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:claude:release
```

The smoke creates a temporary real Git repository, enables
`claude_code_cli:issue_to_patch` only in that validation database, runs
`issue_to_patch` through the configured Claude Code CLI, and fails if the CLI is
missing, the syntax is rejected, the workspace is not a Git worktree, the patch
is empty, QA fails, or evidence artifacts/hashes are missing.

## Risks

- Opus/max effort is not default and requires budget/approval policy.
- Real execution must remain policy-approved and workspace-bound.
- A detected Claude Code binary is only `available` until it advertises an
  `issue_to_patch` contract that can produce patch evidence.

## Limitations

- Usage is estimated unless the CLI exposes exact usage in captured output.
- CLI syntax mismatches are treated as `runtime_unavailable` with the captured
  technical reason, not as completed or generic success.

## Example

`claude_sonnet_developer` maps to a Sonnet-class model with medium effort.
