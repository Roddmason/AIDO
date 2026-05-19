# Architecture Audit

Date: 2026-05-18
Project: AIDO / Local Control Center
Runtime target: Windows-native Python/FastAPI backend with React dashboard.

## Executive Summary

The repository contains a working Python/FastAPI Local Control Center with SQLite
state, jobs, granular action approvals, worker leasing, memory/retrieval, and a
React dashboard. It also contains a large root `src/` tree that appears to be
Claude Code/Anthropic TypeScript CLI source or extracted legacy material. That
tree is not part of the Python backend or the active React dashboard and should
be treated as legacy/audit material until ownership and license provenance are
proven.

The highest-priority issues are source hygiene, license clarity, `store.py`
coupling, FAISS being mandatory, and the lack of first-class workflow/evidence
tables.

## Current Module Map

- `local_control_center/app.py`: FastAPI composition entrypoint.
- `local_control_center/api.py`: root v1 and legacy compatibility routes.
- `local_control_center/store.py`: SQLite facade and remaining god object.
- `local_control_center/worker.py`: compatibility import for the concurrent worker.
- `local_control_center/jobs_approvals/`: jobs, leases, action requests, audit,
  and worker implementation.
- `local_control_center/memory_retrieval/`: memory API, repository, hashing
  embeddings, FAISS/NumPy rebuildable index.
- `local_control_center/agents_runtime.py`: gated planner placeholder.
- `local_control_center/sandbox.py`: Windows command-risk sandbox assessment.
- `local_control_center/workflows/`: workflow definitions, runs, steps, and
  events foundation.
- `local_control_center/security_policy/`: deterministic policy evaluator,
  command classifier, and persisted permission decisions.
- `local_control_center/evidence/`: evidence package creation, QA verdict
  gating, and test-result persistence.
- `local_control_center/agents/`: agent profiles, internal mock runtime,
  model policy catalog, model/cost records, and versionable skill registry.
- `local_control_center/workspaces_projects/`: isolated workspace allocation
  and archive endpoints for task/agent ownership.
- `local_control_center/{pipelines,runtime_integrations,sessions_chats,shared,workspaces_projects}/`:
  slice placeholders that need real ownership in later phases.
- `local-control-center/web/`: active React dashboard, currently JavaScript/JSX.
- `local-control-center/scripts/`: Windows PowerShell launch/autostart/DNS scripts.
- `src/`: legacy or third-party TypeScript CLI surface. Not active core.

## Current Endpoints

Core v1:

- `GET /healthz`
- `GET /api/v1/security/handshake`
- `GET /api/v1/overview`
- `GET /api/v1/events`
- `GET /api/v1/project-templates`
- `GET|POST /api/v1/projects`
- `GET /api/v1/providers`
- `GET /api/v1/teams`
- `GET /api/v1/agents`
- `GET|POST /api/v1/prompts`
- `GET|POST /api/v1/ide-connections`
- `GET /api/v1/open-design`
- `GET|POST /api/v1/jobs`
- `POST /api/v1/jobs/{job_id}/approve`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/jobs/{job_id}/retry`
- `GET /api/v1/approvals`
- `POST /api/v1/jobs/{job_id}/actions/{action_id}/approve`
- `POST /api/v1/jobs/{job_id}/actions/{action_id}/deny`
- `GET|POST /api/v1/memory`
- `GET /api/v1/retrieval/status`
- `POST /api/v1/retrieval/reindex`
- `POST /api/v1/retrieval/search`
- `GET|POST /api/v1/workflows`
- `GET /api/v1/workflows/{id}`
- `POST /api/v1/workflows/{id}/start`
- `POST /api/v1/workflows/{id}/pause`
- `POST /api/v1/workflows/{id}/resume`
- `POST /api/v1/workflows/{id}/cancel`
- `GET /api/v1/policies`
- `POST /api/v1/policies/evaluate`
- `GET|POST /api/v1/evidence`
- `GET /api/v1/evidence/{id}`
- `GET|POST /api/v1/agent-profiles`
- `GET|POST /api/v1/agent-runs`
- `GET /api/v1/skills`
- `POST /api/v1/skills/sync`
- `GET /api/v1/workspaces`
- `POST /api/v1/workspaces`
- `POST /api/v1/workspaces/{id}/archive`
- `GET /api/v1/model-providers`
- `GET|POST /api/v1/model-policies`

Legacy dashboard compatibility:

- `/api/state`
- `/api/workspaces`, `/api/workspaces/select`, `/api/workspaces/{workspace_ref}/{action}`
- `/api/git`, `/api/git/checkout`
- `/api/sessions`, `/api/sessions/select`, session clone/action/patch/delete
- `/api/config/options`, `/api/extensions/catalog`, `/api/config`
- extension marketplace/plugin/skill mutation routes
- `/api/chats/send`, `/api/chats/{chat_id}`
- `/api/idea/intake`
- `/api/pipelines/{pipeline_id}` and pipeline/stage actions

## SQLite Tables

Current schema tables:

- `schema_migrations`
- `projects`
- `workspace_states`
- `teams`
- `agents`
- `sessions`
- `providers`
- `prompt_templates`
- `prompt_versions`
- `memory_items`
- `memory_embeddings`
- `jobs`
- `job_runs`
- `events`
- `audit_events`
- `ide_connections`
- `action_requests`
- `workers`
- `agent_runs`
- `agent_tool_calls`
- `workflows`
- `workflow_runs`
- `workflow_steps`
- `workflow_edges`
- `workflow_events`
- `permission_policies`
- `permission_decisions`
- `evidence_packages`
- `agent_profiles`
- `model_policies`
- `model_calls`
- `cost_usage`
- `workspaces`
- `workspace_allocations`
- `workspace_files`
- `workspace_sessions`
- `git_branches`
- `pull_requests`
- `skills`
- `skill_versions`
- `skill_bindings`
- `artifacts`
- `test_results`
- `qa_verdicts`
- `model_providers`

Still missing target entities after Phase 3-6 foundation:

- deployment/release/risk register tables
- MCP server/tool-call tables
- devcontainer metadata and real git worktree lifecycle records

## `store.py` Coupling

`PlatformStore` remains a compatibility facade but is still too large. It owns:

- SQLite connection setup, schema creation, and seed data.
- project/provider/team/agent/session CRUD.
- workspace JSON import/export compatibility.
- prompt and IDE connection operations.
- overview aggregation.
- facade methods that delegate jobs and memory to slice repositories.

The job and memory SQL ownership has started moving to slices, which is the
right direction. The next extraction should be `shared/db.py` plus focused
repositories for projects, workflows, security decisions, evidence, agents, and
model policies. Do not move `pipelines` first; it is still the most coupled area.

## Frontend State

The active dashboard is a modular React/JSX app under `local-control-center/web`
with feature folders for overview, jobs, memory, agents, sandbox/security,
pipelines, workspaces/sessions, integrations, settings, and design lab. It has
an editorial operational visual direction and avoids external fonts/CDNs.

It is not yet the requested TypeScript/Vite console. The correct next step is to
document the TypeScript target structure, keep the current app passing smoke
tests, and migrate feature-by-feature after backend Phase 2 provides real data.

## Tests

Existing tests cover:

- Python control center API contracts.
- legacy JSON-to-SQLite migration.
- jobs, leases, approvals, worker behavior.
- retrieval rebuild/search.
- vertical slice architecture guardrails.
- frontend architecture/design guardrails.
- Windows local DNS scripts.
- Playwright dashboard smoke tests.

Required additions after this audit:

- workflow creation and step transitions.
- policy decisions and command classification.
- evidence package creation and QA gating.
- agent profile/model policy CRUD.
- frontend TypeScript migration smoke tests when Vite is introduced.

## Dependencies

Python runtime:

- `fastapi`
- `uvicorn`
- `numpy`
- optional: `faiss-cpu`, `openai-agents`

Python development/security tooling:

- `ruff`
- `pre-commit`
- `pip-licenses`
- `semgrep`

JavaScript runtime/build:

- React/ReactDOM
- Radix primitives
- `@xyflow/react`
- `gsap`
- esbuild
- Playwright

PNPM is already configured through `packageManager`.

## License And Provenance Risks

- `package.json` declared `ISC`; that conflicts with the chosen private/no
  commercial strategy and is now treated as incorrect metadata.
- The root `src/` tree contains many Claude Code/Anthropic references and a
  nested `src/node_modules`. Its provenance is not clear enough for AIDO core.
- Do not use the root `src/` tree as implementation source until license and
  ownership are audited. Recommended physical treatment in a later phase:
  `legacy/anthropic-cli-audit/` or remove from the clean repo if it is only
  extracted reference material.
- `faiss-cpu` is OSI-compatible but operationally optional on Windows; it should
  not be a mandatory install for local MVP tests.

## Source Artifacts That Must Not Be Versioned

Observed source artifacts:

- `node_modules/`
- `src/node_modules/`
- `src/` until license provenance and ownership are proven
- `local-control-center/dist/`
- `__pycache__/`
- `.pytest_cache/`
- `.tmp/`
- `test-results/`
- `.venv/`
- local SQLite or WAL files if generated

No root `package-lock.json` was found. `cli.js`/`cli.js.map` were only observed
inside dependency folders during this audit, not as active root source files.

## Prioritized Technical Debt

1. Isolate or remove the root `src/` TypeScript CLI tree from AIDO core.
2. Reduce `PlatformStore` to a facade and move schema ownership into migrations
   plus vertical repositories.
3. Replace broad legacy `/api/*` handlers with `legacy_compat` delegators.
4. Add real git worktree/devcontainer workspace execution after the current
   logical allocation API is stable.
5. Migrate frontend to TypeScript/Vite after the new control-plane endpoints are
   fully represented in the current dashboard.
6. Add recurring license/security scans to quality checks.
