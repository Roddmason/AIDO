# Architecture Audit

Date: 2026-05-19  
Project: AIDO / Local Control Center  
Runtime target: Windows-native Python/FastAPI backend with Vite + React +
TypeScript console.

## Executive Summary

The repository now contains a hard-cutover Python/FastAPI control plane with a
TypeScript frontend. The active product contract is `/api/v1/*` plus `/healthz`.
Removed compatibility routes, JSON runtime imports, JSX entrypoints, esbuild
build script, dependency folders, build outputs, caches, and generated test
artifacts are not source.

The prior store-facade risk is closed. SQLite connection setup, schema
bootstrap, and shared utilities live in `shared/`; FastAPI/CLI runtime
bootstrap uses `control_plane.runtime.ControlCenterRuntime`; active slice APIs
use repositories plus `EventBus` directly. The product `store.py` and the
transitional test store harness have both been removed. Guardrail tests fail if
that pattern reappears in product code or tests.

## Module Map

- `local_control_center/api.py`: FastAPI composition for active v1 routers.
- `local_control_center/app.py`: package entrypoint.
- `local_control_center/cli.py`: Windows/local CLI runner.
- `local_control_center/control_plane/runtime.py`: FastAPI/CLI runtime boundary
  for cwd, DB connection, schema bootstrap, token, and runtime project creation.
- `local_control_center/shared/db.py`: SQLite connection setup, PRAGMAs, and
  transaction helper.
- `local_control_center/shared/time.py`: UTC timestamp helpers.
- `local_control_center/shared/serialization.py`: JSON and stable hash helpers.
- `local_control_center/shared/migrations.py`: additive SQLite schema bootstrap
  and technical seed records.
- `local_control_center/control_plane/`: overview read-model composition.
- `local_control_center/projects/`: project catalog, provider catalog, teams,
  and agent listing repository.
- `local_control_center/jobs_approvals/`: jobs, leases, action requests,
  approvals, audit, worker behavior.
- `local_control_center/memory_retrieval/`: memory metadata and NumPy/FAISS
  retrieval.
- `local_control_center/workflows/`: workflow definitions, runs, steps, edges,
  and workflow events.
- `local_control_center/security_policy/`: command classifier, policy engine,
  permission decisions, sandbox/secrets boundaries.
- `local_control_center/workspaces_projects/`: project discovery and
  task-scoped workspaces.
- `local_control_center/agents/`: profiles, runtime modes, model providers,
  model policies, calls, cost usage, skills, and tool broker.
- `local_control_center/evidence/`: evidence packages, test results, verdicts,
  artifacts.
- `local_control_center/governance/`: risks, decisions, next steps.
- `local_control_center/sessions_chats/`: v1 session and chat read models.
- `local_control_center/pipelines/`: v1 pipeline read models.
- `local_control_center/prompts/`: prompt templates and version records.
- `local-control-center/web/`: active Vite + React + TypeScript console.
- `local-control-center/scripts/`: Windows PowerShell launch, autostart, and
  local DNS helpers.

## Active Endpoints

See `docs/api-contract-matrix.md` for the maintained v1 matrix.

Key surfaces:

- health, handshake, overview, events;
- projects, workspaces, sessions, chats, pipelines;
- jobs, approvals, action requests;
- workflows and workflow transitions;
- agents, runtime providers, model providers, model policies;
- policies, evidence, governance;
- memory and retrieval;
- integrations, MCP registry, and IDE/provider records.

## SQLite Tables

The current schema includes:

- `schema_migrations`
- `projects`
- `teams`
- `agents`
- `sessions`
- `chats`
- `pipelines`
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
- `architecture_decisions`
- `risk_register`
- `next_steps`
- `integrations`
- `mcp_servers`
- `mcp_tool_calls`

Planned but not yet complete:

- deployment and release records;
- devcontainer metadata;
- richer file/diff snapshots before workspace archive.

## Former `store.py` Coupling

`local_control_center/store.py` has been removed from the product package, and
the transitional test store harness has been deleted. Tests now use
`tests_py/control_plane_fixture.py`, which composes `ControlCenterRuntime` and
explicit slice repositories instead of exposing a product-like store facade.

Schema creation and migration seed records now live in
`shared/migrations.py`. SQLite connection PRAGMAs live in `shared/db.py`.
Shared time and JSON/hash helpers live in `shared/time.py` and
`shared/serialization.py`.
Project/provider/team/agent catalog SQL has moved to `projects.repository`.
Project/catalog HTTP routing has moved to `projects.api` and
`projects.commands`; the root `api.py` only composes this router.
IDE connection SQL and HTTP routing have moved to `integrations.repository`
and `integrations.api`.
Prompt template/version SQL and HTTP routing have moved to `prompts.repository`
and `prompts.api`.
Memory/retrieval HTTP commands and the rebuildable NumPy/FAISS index now use
`memory_retrieval.repository.MemoryRepository` and `shared.event_bus.EventBus`
directly instead of routing through a store facade.
Slice routers no longer call `platform.record_event`, `platform.record_audit`,
or `platform.get_project`; a guardrail test enforces use of `EventBus` and
owned repositories. The concurrent worker opens SQLite and uses
`JobsRepository` directly. The gated agents planner records proposed actions
through `JobsRepository`. `api.py` and `cli.py` import `ControlCenterRuntime`.

Direction:

1. keep `control_plane` as the read-model composer;
2. keep tests on explicit repositories rather than a product-like facade;
3. keep the guardrail that blocks reintroducing a store facade.

## Frontend State

The active console is TypeScript and Vite. It includes Overview, Command Center,
Workflows, Jobs & Approvals, Agents, Workspaces, Policy & Security, Memory &
Retrieval, Evidence & QA, Model Gateway, Governance, Integrations, Audit Log,
and Settings.

The console uses local fonts, no CDNs, semantic React/CSS motion with
reduced-motion support, dense tables, drawers, status strips, and backend-driven
state. It does not contain business policy rules.

## Tests

Current coverage includes:

- Python API contracts and hard-cutover guardrails.
- jobs, leases, approvals, and worker behavior.
- workflows, policy decisions, permission profiles, tool-brokered agent calls,
  evidence, agents, model policies, workspaces, skills, and governance.
- local telemetry for HTTP requests, policy decisions, tool calls, model calls,
  and agent runs.
- retrieval rebuild/search with FAISS optional.
- frontend architecture/design guardrails.
- Playwright dashboard smoke tests for desktop/mobile, reduced motion,
  workflows, evidence, and governance.
- Windows local DNS scripts.

## Dependencies

Python runtime:

- `fastapi`
- `uvicorn`
- `numpy`
- optional: `faiss-cpu`, `openai-agents`, OpenTelemetry OTLP/HTTP exporters

Python development/security:

- `ruff`
- `pre-commit`
- `pip-licenses`
- `semgrep`

JavaScript runtime/build:

- React/ReactDOM
- Vite
- TypeScript
- Radix primitives
- `@xyflow/react`
- Playwright
- lucide-react

PNPM is configured through `packageManager`.

## License And Provenance Risks

- The project is private/no-commercial-use for now.
- Core dependencies must remain OSI-compatible or be isolated as optional
  adapters before public release.
- GSAP was removed from the core frontend after license audit because the
  standard GSAP license is not OSI-compatible.
- Optional external runtimes such as OpenHands, SWE-agent, Node-RED, Windmill,
  or Activepieces require separate license and deployment review before
  integration.

## Source Artifacts That Must Not Be Versioned

- `node_modules/`
- `local-control-center/dist/`
- `__pycache__/`
- `.pytest_cache/`
- `.ruff_cache/`
- `.tmp/`
- `test-results/`
- `.venv/`
- `.env`
- local SQLite, WAL, and SHM files.

## Prioritized Debt

1. Continue slimming broad test setup around `tests_py/control_plane_fixture.py`
   where direct repository setup makes test intent clearer.
2. Define explicit approved flows for package installs, writes, and network
   access; low-risk shell allowance is now argument-level and package lifecycle
   hooks require approval.
3. Operation-level OpenAPI request/response aliases are generated and the active
   JSON API client uses generated operation IDs. Remaining DTO precision depends
   on adding explicit Pydantic models to routes that still expose generic
   `dict[str, Any]` schemas.
4. Policy revision diffs are implemented for sandbox profile changes.
