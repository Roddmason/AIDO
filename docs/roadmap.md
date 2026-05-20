# Roadmap

## Completed Foundation

- Audit and private license baseline.
- PNPM/uv quality scripts.
- Optional FAISS with NumPy fallback.
- Clean active API cutover to FastAPI v1.
- Vite + React + TypeScript console.
- Workflow, policy, evidence, agents, model-policy, workspace, skill, risk,
  decision, and next-step tables.
- Internal deterministic policy engine.
- Workspace-aware path gating for policy decisions.
- Internal mock agent runtime.
- Hybrid runtime provider catalog: API, CLI, Ollama, manual, and internal mock.
- Policy-gated tool broker for CLI/API/Ollama/hybrid agent tool calls.
- Restricted subprocess sandbox for allowlisted structured-argv shell calls.
- Docker sandbox posture endpoint and locked-down command-plan builder.
- Agent tool broker can execute policy-allowed Docker sandbox calls using
  `sandbox_profiles` for image catalog, network mode, CPU, memory, and timeout.
- Action approvals create one-use permission grants; sensitive execution must
  provide and consume the matching grant before the broker invokes a sandbox.
- Sandbox profiles are persisted in SQLite and exposed to the API/dashboard.
- Permission grants and sandbox profiles can be revoked with reason, event, and
  audit records.
- Sandbox profile edits use a token-protected, reason-required, audited
  endpoint with MVP network mode locked to `none`.
- Large stdout/stderr from agent tool execution are promoted to hashed evidence
  artifacts before tool-call payloads are persisted.
- Permission profile shell allowlists for `plan`, `dev_safe`, `qa`, and
  `release`.
- Model gateway budget enforcement, provider policy resolution, and secret
  redaction before model-call persistence.
- Project catalog SQL extracted into the `projects` slice.
- IDE connection routes and SQL extracted from root API into
  the `integrations` slice.
- Prompt template routes and versioning SQL extracted from root API into the
  `prompts` slice.
- SQLite schema bootstrap and additive migration seeds extracted from
  the former store path into `shared/migrations.py`.
- SQLite connection PRAGMAs, transaction helper, time helpers, and JSON/hash
  serialization helpers extracted into `shared/db.py`, `shared/time.py`, and
  `shared/serialization.py`.
- Operational event and audit SQL extracted into `shared/event_bus.py`.
- Memory/retrieval commands and the rebuildable index now use
  `MemoryRepository` and `EventBus` directly.
- Project/catalog routes moved from root `api.py` into `projects.api` and
  `projects.commands`.
- Slice APIs no longer use `platform.record_event`, `platform.record_audit`, or
  `platform.get_project`; guardrail tests enforce `EventBus` and owned
  repository use.
- Concurrent workers and the gated agents planner now use `JobsRepository`
  directly.
- FastAPI and CLI bootstrap now use `ControlCenterRuntime`.
- `local_control_center/store.py` has been removed from the product package;
  the old test store harness has also been removed.
- Command classification now uses argument-level allowlists in
  `security_policy/permissions.py`; safe-looking substrings are not enough to
  allow shell execution.
- Workflow details expose linked workspaces, jobs, agent runs, and evidence
  packages through explicit workflow run/step references.
- Docker sandbox adapter execution is wired through the tool broker after
  policy `allow` or a consumed permission grant; results are attached to
  agent-run evidence refs.
- Evidence QA gate with persisted test result records.
- Git worktree allocation and cleanup when the project is a Git repository,
  with safe directory fallback when it is not.
- Workspace archive evidence snapshots for non-Git and degraded workspaces.
- Git worktree archive captures status, name-only files, diff stat, and bounded
  patch before cleanup.
- Large Git patches are persisted as evidence artifacts instead of inline JSON.
- Large execution logs and screenshot payloads are persisted as evidence
  artifacts instead of inline JSON/base64.
- Artifact reads are authenticated, ownership-checked, root-confined, and hash
  verified.
- Artifact cleanup detects and optionally deletes only unreferenced physical
  files under `.tmp/evidence-artifacts`, with event and audit records.
- Artifact retention review surfaces expired referenced artifacts as governance
  risks instead of deleting audit evidence automatically.
- Expired referenced artifacts can be resolved through explicit audited
  `export` or verified physical-file `delete` retention actions while retaining
  SQLite artifact rows.
- MCP server registry and optional OpenHands/SWE-agent adapter status endpoints
  exist without making those runtimes core dependencies.
- Governance ledger exposed through API and dashboard.
- Event and approval drawers in the TypeScript console.
- Automatic governance risks for policy-gated actions, blocked/failed QA, and
  cancelled workflows.
- JUnit XML and pytest summary ingestion into normalized test result records,
  with unsafe XML declarations rejected at the API boundary.
- Evidence packages can be exported as token-protected Markdown QA reports
  without leaking local artifact paths.
- Post-creation evidence artifact ingestion supports bounded text/base64
  payloads without accepting client-supplied filesystem paths.
- Local telemetry events now cover FastAPI requests, policy decisions, tool
  calls, model calls, and agent run completion with redaction and correlation
  ids.
- Optional OTLP/HTTP OpenTelemetry exporters can send redacted traces and event
  counters to external collectors when explicitly configured.
- MCP, OpenHands, and SWE-agent execution hooks are wired behind the tool broker,
  agent `allowedTools`, policy decisions, approvals, and evidence records.
- Agent profile and model policy configuration is available through strict
  TypeScript forms and backend catalog validation instead of manual JSON edits.
- Workflow, governance, sandbox profile, and MCP registry configuration is
  available through strict TypeScript forms and backend validation.
- Opt-in runtime adapter and OTEL exporter smoke scripts exist for local/CI
  profiles without making Docker, OpenHands, SWE-agent, or MCP core
  dependencies.
- Runtime adapter smoke now auto-detects installed OpenHands and SWE-agent CLIs
  and routes `--version` checks through broker, policy, and sandbox before any
  deeper release profile runs.
- GitHub Actions quality workflow runs default tests/build/lint and exposes
  explicit workflow-dispatch switches for runtime and OTEL smokes.
- The frontend has a generated OpenAPI endpoint map checked in at
  `web/src/api/generated/openapi.ts`, with CI drift detection.
- The generated OpenAPI client now exposes operation-id lookup, path
  interpolation, token-aware request helpers, and operation-level type aliases
  for v1 routes.
- Command palette and workflow inspector are backed by FastAPI v1 state rather
  than client-invented data.
- The command palette can create workflows, focus pending approvals, and open a
  searchable operational event ledger.
- Keyboard shortcuts now open operational surfaces without hidden mutations:
  `Ctrl+Alt+A` for approvals, `Ctrl+Alt+E` for events, and `Ctrl+Alt+W` for
  workflows.
- Workflow inspection now includes linked policy decisions and evidence
  artifacts, including artifact names, kind, and hashes.
- Evidence and workflow inspectors can preview and download artifacts through
  the authenticated, root-confined artifact endpoint.
- Sandbox policy edits create explicit `policy_revisions` records with
  previous/updated snapshots and changed-field lists.
- Policy & Security shows visual policy revision diffs for sandbox profile
  changes.

## Next Backend Work

1. Keep tightening tests around direct repositories and
   `tests_py/control_plane_fixture.py` where broad fixture setup is still
   noisier than the behavior under test.
2. Replace the remaining hand-written frontend response shapes with
   schema-derived request/response DTOs once the v1 schema stops changing
   daily.
3. Add deeper issue-to-patch installed-runtime fixtures for OpenHands/SWE-agent
   in a separate release validation profile after their local CLI contracts are
   pinned.

## Next Frontend Work

1. Replace remaining hand-written frontend response shapes with schema-derived
   request/response DTOs once the v1 schema stops changing daily.
