# AIDO / Local Control Center

AIDO is a Windows-native local control plane for software-development agents.
It is not a chat clone and not an autonomous free-for-all. The product goal is
to control workflows, approvals, isolated workspaces, policy decisions, evidence,
cost records, and audit events around agent-assisted engineering.

## Current Status

- Backend: Python/FastAPI with SQLite as the canonical local store.
- Frontend: Vite + React + TypeScript console built with PNPM.
- Package management: PNPM for JavaScript, `uv` for Python.
- License: private/proprietary for now. Core dependencies are kept compatible
  with a possible future open-source release.
- Runtime target: Windows local development. Docker is optional, not required.

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

- It is not an open-source release yet.
- It is not a production deployment platform.
- It does not permit dangerous shell execution without policy gates.
- It does not require WSL.
- It does not use Node as backend business runtime.
- It does not keep compatibility routes as active product surface.

## Setup

```powershell
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
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run test:py
corepack pnpm@10.24.0 run test:web
corepack pnpm@10.24.0 run quality
```

Direct backend:

```powershell
uv run python -m local_control_center --dashboard-host 127.0.0.1 --dashboard-port 4310
```

## Security

Mutating API calls require the loopback handshake token. Shell and tool actions
are evaluated through the internal policy engine. Critical actions require human
approval; low-risk test/build/read-only actions can be allowed by policy.

Never commit `.env`, local SQLite databases, build outputs, dependency folders,
or generated artifacts.

## Documentation

- `docs/architecture-audit.md`
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
