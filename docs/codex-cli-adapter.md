# Codex CLI Adapter

## What It Does

`CodexCliRuntime` detects and builds safe non-interactive Codex CLI commands for workspace-bound coding runtime sessions.

## Configuration

- `runtime_installations` stores the Codex executable path, version, capabilities,
  preferred roles, health, last validation, and configuration source.
- `runtime_accounts` seeds `Local Codex CLI` with
  `authMode=provider_native_cli`; AIDO uses the Codex CLI native login/session
  by default instead of copying API keys.
- `AIDO_CODEX_COMMAND=codex` is a deprecated bootstrap/CI override, mainly for
  release smoke scripts.
- `AIDO_ENABLE_CLI_RUNTIMES=false`

Profiles seeded in the catalog include `codex_gpt55_developer`, `codex_gpt55_reviewer` and `codex_gpt55_xhigh_architect`.

## Endpoints

- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/codex_cli/detect`
- `POST /api/v1/model-gateway/cli-runtimes/codex_cli/health-check`

## Testing

Tests verify missing binary status and dangerous flag rejection.

Release validation is a real opt-in smoke, not part of the unit suite:

```powershell
# Deprecated CI/bootstrap override; normal runtime config lives in SQLite.
$env:AIDO_CODEX_COMMAND = "codex"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:codex:release
```

The smoke creates a temporary real Git repository, runs `issue_to_patch` through
the configured Codex CLI, and fails the release validation if Codex is missing,
the CLI syntax no longer matches the adapter, the workspace is not a Git
worktree, the patch is empty, QA fails, or evidence artifacts/hashes are
missing.

## Risks

- Real execution is intentionally disabled by default.
- The adapter must not modify global user Codex configuration.

## Limitations

- Usage parsing is best-effort and only consumes parseable JSON/JSONL events. Supported shapes include `usage`, `token_usage`, `tokens`, `message.usage`, `metrics.token_usage` and `llm_metrics`; free-text output still records no fabricated usage.

## Example

Generated commands include workspace-write sandbox and on-request approvals.
