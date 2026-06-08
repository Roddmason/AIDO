# AIDO Current State Analysis

Date: 2026-05-24
Branch: `codex/aido-control-plane-hardening`

## Summary

AIDO already contains a serious local-first AI SDLC control plane. The current
work is not a rebuild. The correct direction is hardening the existing FastAPI,
SQLite, React and Model Gateway implementation.

Latest observed baseline before this iteration:

- Python: `uv run pytest tests_py -q` -> 203 passed.
- Web: `corepack pnpm@10.24.0 run test:web` -> 44 passed.
- Lint: `corepack pnpm@10.24.0 run lint` -> passed.
- Security: `security:secrets` and `security:sast` -> 0 findings.

Final verification after this hardening iteration:

- Python: `uv run pytest tests_py -q` -> 213 passed.
- Web: `corepack pnpm@10.24.0 run test:web` -> 44 passed.
- TypeScript, build, lint, secrets scan and SAST all passed.

## Backend State

- FastAPI API v1 exists and is modular by domain.
- `ControlCenterRuntime` owns local SQLite, event bus, telemetry, jobs and
  local state.
- Vertical slices exist for projects, jobs, approvals, workflows, agents,
  policy, workspaces, retrieval, evidence, governance, integrations, sessions
  and pipelines.
- Unified Model & Runtime Gateway already exists under
  `local_control_center/agents`.
- `model_gateway_api.py` exposes provider, model, routing, role policy, usage,
  budget, provider limit, benchmark and CLI session endpoints.
- Provider adapters exist for NVIDIA NIM, OpenAI API, Anthropic API,
  OpenAI-compatible endpoints, OpenRouter, Ollama and optional LiteLLM.
- CLI runtime adapters exist for Codex CLI, Claude Code CLI, OpenHands,
  SWE-agent and manual routing.

## Frontend State

- Vite, React and TypeScript are active under `local-control-center/web`.
- The app already has navigation sections for Overview, Command Center,
  Workflows, Jobs & Approvals, Agents, Workspaces, Policy & Security, Memory,
  Evidence & QA, Model Gateway, Governance, Integrations, Audit Log and
  Settings.
- Model Gateway already renders operational sections for provider accounts,
  model catalog, routing profiles, role assignments, route preview, usage,
  budgets, provider limits, routing decisions, CLI sessions, benchmarks and
  settings.
- The page was still too monolithic, so this iteration splits panel shells into
  dedicated feature components while keeping behavior stable.

## Tests State

- Python tests were green at 203 passed before hardening.
- Web tests were green at 44 passed before hardening.
- The old Windows selector event loop failure is not present in the observed
  baseline.
- New guardrails now cover AIDO docs, optional FAISS requirements, expanded
  roles, provider metadata, `usage_source`, budget/quota output and CLI session
  persistence.

## DB And Migrations State

- SQLite is the canonical local store.
- Migrations are additive and preserve legacy tables.
- Existing legacy/read-model tables include `model_providers`, `model_policies`,
  `model_calls` and `cost_usage`.
- Phase 12 already created gateway tables:
  `provider_accounts`, `model_catalog`, `routing_profiles`,
  `role_model_policies`, `usage_ledger`, `provider_limits`, `budget_rules`,
  `routing_decisions`, `cli_sessions`, `runtime_capabilities`,
  `provider_health_checks` and `model_benchmarks`.
- This iteration adds `provider_accounts.metadata_json TEXT DEFAULT '{}'` and
  `usage_ledger.usage_source TEXT DEFAULT 'estimated'`.

## Agents State

- Agent contracts are executable profiles, not free-form personas.
- Profiles include role, runtime, permissions, provider/runtime allowlists,
  routing profile, role policy, token limits and approval threshold.
- Roles existed for analyst, product owner, technical lead, developer,
  implementer, QA, QA reviewer, security reviewer and release manager.
- The catalog is now extended with `technical_lead_shadow`,
  `backend_engineer` and `frontend_engineer`.

## Model Gateway State

- Default routing mode is `balanced_best_value`.
- Route preview already chooses provider/model/runtime using role policy,
  mode, capability, health, context, privacy, code-edit requirements, pricing
  and quota checks.
- This iteration adds explicit `budgetResult` and `quotaResult` to route
  preview responses and persists richer routing decisions.
- Cost and usage are tracked in `usage_ledger`; legacy `cost_usage` remains a
  compatible read-model.

## CLI And Runtime State

- Runtime adapters are optional and detection based.
- Real CLI execution is fail-closed unless `AIDO_ENABLE_CLI_RUNTIMES=true`.
- Dangerous flags are blocked, including bypass/yolo/full-access variants.
- This iteration persists CLI runtime attempts in `cli_sessions` with redacted
  command/env policy and linked `usage_ledger` rows. Runtime execution remains
  fail-closed when no executable runtime is available.

## Workspaces State

- Workspaces are first-class SQLite records with project/task/agent ownership,
  path, status, isolation type and metadata.
- Git worktree support exists.
- Real CLI execution is constrained to the provided workspace and can validate
  the registered workspace path before execution.

## Security Policy State

- Policy engine classifies shell/git/deploy/MCP/runtime activity.
- Production deploy requires explicit human approval.
- Direct main edits, force push and dangerous runtime flags are blocked or
  gated.
- Secrets are redacted in provider accounts, usage, events, logs and CLI
  session data.

## Evidence State

- Evidence packages, artifacts, test results and QA verdicts exist.
- Tool execution evidence links stdout/stderr artifacts when produced by the
  sandbox path.
- CLI runtime session persistence now records usage and leaves artifact columns
  ready for stdout/stderr/log IDs when runtime execution creates them.

## Governance State

- Risks, decisions and next steps exist as governance records.
- Audit events are recorded through the local event bus.
- Routing decisions and approvals are auditable.

## Integrations And MCP State

- MCP registry and gateway primitives exist.
- Integrations are local-first and do not require n8n as a core dependency.
- External providers remain disabled unless configured.

## Memory And Retrieval State

- SQLite memory metadata is canonical.
- Retrieval supports FAISS when installed and falls back to NumPy when FAISS is
  unavailable.
- `faiss-cpu` is optional through `pyproject.toml` extra `faiss`; it must not be
  a mandatory `requirements-python.txt` dependency.

## Package Metadata And License State

- Root `package.json` is named `aido-local-control-center`.
- License is `MIT`, consistent with repository license docs and tests.
- Existing package metadata no longer declares `claude-rebuild`.

## Source Artifact State

Ignored local artifacts can exist in the working tree, but none should be
versioned:

- `node_modules/`
- `.venv/`
- `.tmp/`
- `dist/`
- `__pycache__/`
- `.pytest_cache/`
- `.ruff_cache/`
- `test-results/`

`git ls-files` showed no versioned entries for those artifact paths.

## Technical Risks

- Model Gateway page still contains significant orchestration logic even after
  panel shell extraction.
- Pricing catalog seeds are placeholders and must not be treated as real-time
  price truth.
- CLI stdout/stderr artifact capture is not yet a full artifact writer for all
  runtime paths.
- Benchmark-driven routing is seeded but not yet mature enough to dominate
  routing decisions.

## Security Risks

- Real provider calls and real CLI runtimes must remain disabled by default.
- Provider credentials must stay as refs such as env vars, never raw keys.
- Budget/quota checks must run before any real call.
- Workspace registration must be enforced for real CLI execution.
- Remote providers must stay blocked for `local_private` routing.
