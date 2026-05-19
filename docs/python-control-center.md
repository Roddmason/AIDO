# Python Control Center Architecture

This cut introduces a Windows-native Python backend while keeping the existing React dashboard.

## Runtime

- `uv run python -m local_control_center` starts FastAPI and serves the built React dashboard from `local-control-center/dist/web`.
- `local-control-center/scripts/start-control-center.ps1` is the Windows entrypoint used by `pnpm start` and Task Scheduler.
- `local-control-center/scripts/register-autostart.ps1` registers a user-scoped scheduled task. This is intentional: user-profile CLI auth and direct project access are more reliable than a Windows Service for this local agent.
- `local-control-center/scripts/register-local-dns.ps1` registers local Windows hosts aliases for testing: `local-control-center.test` and `lcc.test`.
- `local-control-center/scripts/unregister-local-dns.ps1` removes only the marked Local Control Center DNS block.
- The previous Node.js backend source is archived under `Legacy/Node.js/local-control-center`. Node remains only as the React/esbuild toolchain.

## Local DNS For Testing

Use a reserved `.test` hostname through the Windows hosts file:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/register-local-dns.ps1
pnpm start
```

Then open:

- `http://local-control-center.test:4310`
- `http://lcc.test:4310`

The registration script is idempotent, flushes the DNS resolver cache, and self-elevates with UAC because `C:\Windows\System32\drivers\etc\hosts` requires Administrator rights. It writes a marked block only:

```text
# BEGIN Local Control Center DNS
127.0.0.1    local-control-center.test
127.0.0.1    lcc.test
::1          local-control-center.test
::1          lcc.test
# END Local Control Center DNS
```

To remove it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/unregister-local-dns.ps1
```

## Package Management

- JavaScript dependencies are managed with PNPM through Corepack: `corepack pnpm@10.24.0 install`.
- Do not use `npm install` in this repository. `package-lock.json` is intentionally absent; `pnpm-lock.yaml` is authoritative.
- Python dependencies are managed with uv: `uv sync --extra test`.
- Useful commands:
  - `pnpm run build:control-center`
  - `pnpm run test:py`
  - `pnpm run test:all`
  - `pnpm start`

## Store

SQLite remains canonical at `%USERPROFILE%\.claude\local-control-center\platform.sqlite` unless `LOCAL_CONTROL_CENTER_DB` or `--db-path` overrides it. Legacy `.claude/team-workspace.json` is imported into SQLite and then treated as backup/export material.

## Vertical Slices

The backend is moving away from horizontal framework modules. New business logic should live in domain slices:

- `local_control_center/jobs_approvals`: job queue, leases, runs, events, audit, and granular action approvals.
- `local_control_center/memory_retrieval`: memory records, embedding metadata, FAISS/NumPy retrieval index, and retrieval APIs.
- `local_control_center/workspaces_projects`, `sessions_chats`, `pipelines`, `runtime_integrations`, `security_policy`, and `legacy_compat`: explicit domain boundaries reserved for the next extraction passes.
- `local_control_center/app.py`: FastAPI composition entrypoint used by the CLI.

Keep route handlers thin. `api.py` files should parse HTTP input and call `commands.py`; repositories own SQL for their slice. Do not add new generic `services.py`, `helpers.py`, or cross-domain SQL in `local_control_center/store.py`. `PlatformStore` may remain as a compatibility facade while slices are extracted.

Core tables include:

- `projects`, `teams`, `agents`, `sessions`, `workspace_states`
- `jobs`, `job_runs`, `events`, `audit_events`
- `action_requests` for granular approvals
- `memory_items`, `memory_embeddings` for retrieval metadata
- `agent_runs`, `agent_tool_calls` for gated Agents SDK integration

## Workers

Workers claim queued jobs with SQLite `BEGIN IMMEDIATE`, leases, and `job_runs`. Expired leases are requeued before each batch. Sensitive jobs create `action_requests` and remain blocked until each pending action is explicitly approved.

## Retrieval

`RetrievalIndex` rebuilds from `memory_items`. FAISS is a runtime dependency through `faiss-cpu`; `/api/v1/retrieval/status` reports `degraded: true` only if FAISS cannot be imported and the NumPy fallback is active.

## Security

Mutating API routes require `X-Local-Control-Token` from `/api/v1/security/handshake`. Job-level approval is preserved for compatibility, but it does not bypass pending granular `action_requests`.
