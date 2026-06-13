# Python Control Center Architecture

AIDO is a Windows-native Python/FastAPI control plane with a Vite + React +
TypeScript console.

## Runtime

- `uv run python -m local_control_center` starts FastAPI and serves the built
  React console from `local-control-center/dist/web`.
- `local-control-center/scripts/start-control-center.ps1` is the Windows
  entrypoint used by PNPM scripts and Task Scheduler.
- `local-control-center/scripts/register-autostart.ps1` registers a user-scoped
  scheduled task. This preserves user-profile CLI auth, PATH, mapped drives,
  and direct access to local projects.
- `local-control-center/scripts/register-local-dns.ps1` optionally registers
  `local-control-center.test` and `lcc.test` in the Windows hosts file for
  browser testing.
- Node is only the frontend build toolchain. Backend business logic is Python.

## Local DNS For Testing

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/register-local-dns.ps1
corepack pnpm@10.24.0 run start
```

Then open:

- `http://local-control-center.test:4310`
- `http://lcc.test:4310`

The registration script writes only a marked block and flushes the DNS resolver
cache. Remove it with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/unregister-local-dns.ps1
```

## Package Management

- JavaScript dependencies: `corepack pnpm@10.24.0 install`.
- Python dependencies: `uv sync --extra dev --extra test`.
- Optional FAISS: `uv sync --extra faiss --extra dev --extra test`.
- Do not use `npm install`; `pnpm-lock.yaml` is authoritative.

## Store

SQLite remains canonical at
`%USERPROFILE%\.claude\local-control-center\platform.sqlite` unless
`LOCAL_CONTROL_CENTER_DB` or `--db-path` overrides it.

Runtime startup no longer imports JSON state. Historical JSON migration should
be a manual utility outside the normal server path.

## Vertical Slices

- `jobs_approvals`: job queue, leases, runs, events, audit, and granular action
  approvals.
- `memory_retrieval`: memory records, persisted real embedding metadata,
  rebuildable FAISS/NumPy indexes, and retrieval APIs.
- `sessions_chats`: v1 session and chat read models.
- `pipelines`: v1 pipeline read models.
- `prompts`: prompt templates and append-only prompt version history.
- `workflows`: SDLC workflow graph, runs, steps, and transition state.
- `agents`: agent profiles, runtime modes, model policies, providers, calls,
  costs, and skills.
- `security_policy`: deterministic policy engine, command classifier, sandbox
  posture, and permission decisions.
- `workspaces_projects`: task-scoped workspace allocation and Git worktree
  support.
- `evidence`: evidence packages, test result records, and QA verdict gates.
- `governance`: risks, decisions, next steps, and ownership.
- `integrations`: IDE connections, MCP server registry, and optional adapter
  status for external runtimes such as OpenHands and SWE-agent.

Route handlers stay thin. `api.py` files parse transport input and call command
or repository functions. Slice repositories own operational SQL, while SQLite
connection setup, additive schema bootstrap, time helpers, and JSON/hash helpers
live in `shared/db.py`, `shared/migrations.py`, `shared/time.py`, and
`shared/serialization.py`. Memory/retrieval commands compose
`MemoryRepository`, `EventBus`, and `RetrievalIndex` directly. Project/catalog
routes live in the `projects` slice, and routers use `EventBus` instead of the
store facade for events/audit records. FastAPI and CLI bootstrap use
`ControlCenterRuntime`, which exposes only cwd, DB connection, schema init,
loopback token, and runtime project creation. The former store facade and its
transitional test harness have been removed.

## Workers

Workers open SQLite directly, use `JobsRepository` for leases and `job_runs`,
emit events through the jobs repository/event bus, and requeue expired leases
during recovery. Sensitive work creates action requests and stops until the
specific action receives an approval reason.

## Retrieval

FAISS is optional, and NumPy is only a rebuildable vector-index backend.
Neither backend generates embeddings or acts as a semantic fallback. If a
project has no persisted real embeddings, retrieval reports
`configuration_required` and search returns no memory context. SQLite memory
metadata remains canonical; index files can be rebuilt per project from active,
non-deleted, non-expired memory embeddings.

## Security

Mutating routes require `X-Local-Control-Token` from
`/api/v1/security/handshake`. Shell, CLI, API, and Ollama-driven agent actions
must go through policy, approvals, sandbox decisions, audit, and evidence.
Low-risk shell allowance is argument-level: `dev_safe` can run approved test,
build, lint, diagnostics, and read-only commands inside the workspace, while
package-manager installs, unknown scripts, and lifecycle hooks require approval.
