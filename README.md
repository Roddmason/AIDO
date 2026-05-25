# AIDO / Local Control Center

AIDO is a local-first native control plane for software-development agents on
Windows, Linux and macOS.
It is not a chat clone and not an autonomous free-for-all. The product goal is
to control workflows, approvals, isolated workspaces, policy decisions, evidence,
cost records, and audit events around agent-assisted engineering.

## Current Status

- Backend: Python/FastAPI with SQLite as the canonical local store.
- Frontend: Vite + React + TypeScript console built with PNPM.
- Package management: PNPM for JavaScript, `uv` for Python.
- License: MIT. AIDO is open source; core dependencies remain OSI-compatible
  or isolated as optional adapters.
- Runtime target: native OS process on Windows, Linux and macOS. Docker is
  optional, not required.

## What It Is

- Local AI SDLC control plane.
- Durable job and approval surface.
- Policy-gated command and tool execution foundation.
- Workflow, workspace, agent, model-policy, skill, memory, and evidence backend
  foundation.
- Governance ledger for architecture decisions, risk register entries, and
  prioritized next steps.
- Hybrid agent runtime catalog per agent: API, CLI, Ollama, manual, or internal
  mock, always behind policy and evidence controls.
- Automatic risk creation from policy-gated actions, failed/blocked QA, and
  cancelled workflows.
- Workspace-aware path policy and task-scoped Git worktree allocation when the
  project is a Git repository.

## What It Is Not

- It is not a production deployment platform.
- It does not permit dangerous shell execution without policy gates.
- It does not require WSL.
- It does not use Node as backend business runtime.
- It does not keep compatibility routes as active product surface.

## Setup

AIDO's frontend toolchain targets Node >=24.16.0 <25.0.0 with PNPM 10.24.0.
Use the repository `.nvmrc` and helper script before running web builds or
tests so engine warnings indicate a real environment mismatch rather than an
implicit downgrade.

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/use-node.ps1
corepack enable
corepack pnpm@10.24.0 install
uv sync --extra dev --extra test
```

Linux/macOS shell:

```bash
nvm install 24.16.0
nvm use 24.16.0
corepack enable
corepack pnpm@10.24.0 install
uv sync --extra dev --extra test
```

Optional FAISS install:

```powershell
uv sync --extra faiss --extra dev --extra test
```

## Common Commands

```powershell
corepack pnpm@10.24.0 run start
uv run python local-control-center/scripts/start_control_center.py --dashboard-host 127.0.0.1 --dashboard-port 4310
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run openapi:generate
corepack pnpm@10.24.0 run test:py
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run quality
```

Direct backend:

```powershell
uv run python -m local_control_center --dashboard-host 127.0.0.1 --dashboard-port 4310
```

Native process startup:

- Cross-platform default:
  `uv run python local-control-center/scripts/start_control_center.py`
- Windows wrapper:
  `corepack pnpm@10.24.0 run start:windows`
- Default URL: `http://127.0.0.1:4310`
- Stop: press `Ctrl+C` in the terminal that owns the process.

The native launcher builds the dashboard when missing, serves the built
frontend through FastAPI, and sets safe defaults:
`AIDO_ENABLE_REAL_PROVIDER_CALLS=false`,
`AIDO_ENABLE_CLI_RUNTIMES=false`, and `AIDO_REDACT_SECRETS=true`.

Optional local smokes:

```powershell
$env:AIDO_RUNTIME_SMOKE = "1"
local-control-center/scripts/smoke-runtime-adapters.ps1

$env:AIDO_OTEL_SMOKE = "1"
local-control-center/scripts/smoke-otel-exporter.ps1
```

The smoke scripts skip unless explicitly opted in. OpenHands, SWE-agent, MCP
servers, Docker, and external OTEL collectors are optional adapters, not startup
requirements.

This repository intentionally does not ship a GitHub quality workflow. Run
quality checks locally before pushing. Repository rulesets protect branches and
require pull request review for protected work. The owner may push directly to
`dev` for local-first integration work.

```powershell
local-control-center/scripts/protect-main-branch.ps1
```

The script name is retained for compatibility; it now manages the protected
branch ruleset for all branches except `dev`.

## Security

Mutating API calls require the loopback handshake token. Shell and tool actions
are evaluated through the internal policy engine. Critical actions require human
approval; low-risk test/build/read-only actions can be allowed by policy.

Never commit `.env`, local SQLite databases, build outputs, dependency folders,
or generated artifacts.

## Documentation

- `docs/architecture-audit.md`
- `docs/project-map.md`
- `docs/backend.md`
- `docs/security-policy.md`
- `docs/workflows.md`
- `docs/workspaces.md`
- `docs/agents.md`
- `docs/evidence.md`
- `docs/governance.md`
- `docs/frontend.md`
- `docs/license-audit.md`
- `docs/development.md`
- `docs/credentials.md`

## Contributing

Contributions are welcome through pull requests. Keep changes small, include
tests for behavior changes, do not commit secrets or generated artifacts, and
expect protected branches to require owner review before merge.
