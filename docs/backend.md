# Backend Architecture

The backend is a Python/FastAPI control plane organized around vertical slices.
`PlatformStore` remains a temporary compatibility facade while new ownership
moves into feature repositories.

## Active Slices

- `jobs_approvals`: jobs, runs, leases, action requests, approvals, audit.
- `control_plane`: operational overview read model composed from slice state.
- `memory_retrieval`: SQLite memory metadata plus rebuildable NumPy/FAISS index.
- `workflows`: workflow definitions, runs, steps, edges, and workflow events.
- `security_policy`: command classification, deterministic decisions, persisted
  permission decisions.
- `workspaces_projects`: task-scoped workspace allocation, safe Git worktree
  creation/degradation, and archive lifecycle.
- `agents`: agent profiles, internal mock runtime, model policies, model/cost
  records, versionable skills.
- `evidence`: evidence packages, test results, and QA verdict gates.
- `governance`: architecture decisions, risk register, and execution next
  steps with audit-backed updates.
- `legacy_compat`: isolated adapters for old dashboard `/api/*` contracts.

## Store Direction

`store.py` should keep compatibility methods only until each slice owns its SQL.
The operational overview read model now lives in `control_plane`, not inside
`PlatformStore`. New domain writes should go through slice repositories, not
through new generic methods on `PlatformStore`.

## API Composition

`local_control_center.api.create_app()` composes routers and should be the only
FastAPI creation path. The package intentionally avoids creating a global app at
import time so tests and CLI runs can inject an isolated store safely.

Legacy dashboard routes are no longer embedded in the root API module. They are
mounted from `legacy_compat` so active v1 development does not accumulate
compatibility code in the composition layer. Mutating compatibility routes also
require the loopback write token; compatibility is not allowed to bypass local
security policy.

## Migration Policy

Migrations are additive. Existing tables are not dropped. Rebuildable indexes
such as FAISS are not source of truth; SQLite remains canonical.

Current migration versions:

- v1: baseline platform schema.
- v2: workflows, policy decisions, evidence, agents, model policies.
- v3: workspaces, skills, artifacts, test results, QA verdicts, model providers.
- v4: workflow traceability columns for allocated workspaces.
- v5: architecture decisions, risk register, and next steps.
