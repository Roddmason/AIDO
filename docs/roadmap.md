# Roadmap

## Completed Foundation

- Audit and MIT open-source license baseline.
- PNPM/uv quality scripts.
- Optional FAISS; NumPy remains an index backend only over persisted real
  embeddings.
- Clean active API cutover to FastAPI v1.
- Vite + React + TypeScript console.
- Workflow, policy, evidence, agents, model-policy, workspace, skill, risk,
  decision, and next-step tables.
- Internal deterministic policy engine.
- Workspace-aware path gating for policy decisions.
- Real runtime provider catalog: API, CLI, Ollama, and manual, with test
  simulators excluded from product provider APIs.
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
- Opt-in runtime adapter and OTEL exporter smoke scripts exist for local/release
  profiles without making Docker, OpenHands, SWE-agent, or MCP core
  dependencies.
- Runtime adapter smoke now auto-detects installed OpenHands and SWE-agent CLIs
  and routes `--version` checks through broker, policy, and sandbox before any
  deeper release profile runs.
- Runtime adapter release-validation smoke now has a strict mode:
  `AIDO_RUNTIME_RELEASE_VALIDATION=1` forces `issue_to_patch` smoke and fails
  unless OpenHands/SWE-agent argv and issue text variables are supplied and the
  configured executable resolves on the runner.
- OpenHands and SWE-agent adapters now expose versioned issue-to-patch
  contracts and reject missing `issueText`, missing workspace path, malformed
  `argv`, wrong executable, and dangerous runtime flags before install
  detection or execution.
- The optional runtime smoke profile can run OpenHands/SWE-agent
  `issue_to_patch` through the broker when release validation explicitly
  provides the installed CLI argv and issue text.
- GitHub Actions quality workflow has been removed by policy; quality checks are
  local/release-runner commands, and protected branches are guarded by a GitHub
  repository ruleset applied by `protect-repository-branches.ps1`.
- The frontend has a generated OpenAPI endpoint map checked in at
  `web/src/api/generated/openapi.ts`, with local drift detection through
  `pnpm run openapi:generate` plus `git diff`.
- The generated OpenAPI client now exposes operation-id lookup, path
  interpolation, token-aware request helpers, and operation-level type aliases
  for v1 routes.
- The OpenAPI client generator now emits schema-derived
  `OperationRequestBody<T>` and `OperationResponse<T>` aliases, and the active
  frontend API client uses generated operation IDs for JSON API calls instead
  of raw route literals.
- High-traffic read endpoints now have explicit Pydantic response models for
  health, loopback handshake, and retrieval status, so the generated frontend
  DTOs use `HealthResponse`, `HandshakeResponse`, and
  `RetrievalStatusResponse` instead of `JsonObject` fallbacks.
- High-traffic mutating routes for workflow creation/status changes, agent
  profile upserts, and model policy upserts now expose Pydantic request/response
  contracts, so generated operation request bodies are no longer `unknown`.
- Jobs and approvals mutations now expose Pydantic contracts for job creation,
  job approve/cancel/retry, and granular action approve/deny operations.
- Governance mutations now expose Pydantic contracts for architecture
  decisions, risks, and next steps, so generated operation request bodies no
  longer fall back to `unknown`.
- Workspace allocation/archive, MCP registration, and IDE connection upsert
  routes now expose Pydantic contracts, reducing another set of frontend
  hand-written mutation shapes.
- Policy evaluation, permission-grant revocation, sandbox profile revocation,
  and sandbox profile edits now expose Pydantic contracts while preserving the
  backend-owned policy gate and reason-required audit path.
- Project creation, session/chat creation, pipeline creation, memory creation,
  retrieval search/reindex, and prompt upserts now expose Pydantic contracts,
  reducing the remaining operator-facing JSON-free configuration gap.
- Agent-run creation, skill sync, evidence package creation, artifact ingest,
  artifact cleanup, and artifact retention actions now expose Pydantic
  contracts. The generated OpenAPI client no longer has `unknown` request-body
  entries for active v1 routes.
- The OpenAPI client generator tolerates Windows temporary-directory cleanup
  races after closing SQLite-backed runtime state.
- Active v1 read operations now declare Pydantic response models, and the
  generated `OperationResponseBodies` map no longer falls back to raw
  `JsonObject` operation responses. A guardrail test fails if a route regresses
  to an untyped response body.
- Governance read/mutation payloads now use row-level DTOs for architecture
  decisions, risks, and next steps; the generated client also rejects
  `Array<never>` regressions for schema arrays with intentionally flexible JSON
  values.
- Jobs, granular action requests, events, and audit events now have row-level
  DTOs for list and mutation responses, tightening the highest-traffic
  operational lane in the generated client.
- Project catalog and workspace allocation/archive responses now use row-level
  DTOs, including project templates, providers, teams, catalog agents, and
  isolated workspace records.
- Evidence package, test-result, and artifact read/mutation responses now use
  row-level DTOs, so QA/evidence surfaces no longer depend on generic response
  rows for their stable records.
- The primary overview read model now reuses row-level DTOs for projects,
  jobs, action requests, runtime workspaces, evidence, and governance records,
  reducing frontend refinement risk on the dashboard's highest-traffic query.
- Sessions, chats, pipelines, memory items, workflows, policy records, agent
  profiles, model policies/providers, and agent/model run telemetry now expose
  row-level DTOs in v1 read responses and the generated client instead of broad
  `JsonObject` rows where their schema is stable.
- Frontend domain aliases now point at generated OpenAPI DTOs for stable
  records instead of maintaining hand-written duplicate TypeScript shapes.
- Shared event-bus tests now use direct SQLite/repository setup instead of the
  broad app fixture; an architecture guardrail prevents that regression.
- Repository-level telemetry tests now use direct SQLite/repository setup while
  keeping the broad app fixture only for HTTP middleware coverage.
- Schema migration tests now initialize SQLite migrations directly instead of
  booting the full control-plane fixture; a guardrail keeps schema checks out
  of the app composition path.
- Model gateway repository tests now use direct SQLite/migration setup and
  repository construction instead of the full control-plane fixture.
- Retrieval-index and base schema tests now use direct SQLite/migration setup
  with owned repositories instead of the full app fixture.
- Gated agents planner tests now construct `JobsRepository` on direct SQLite
  setup instead of relying on the full control-plane fixture.
- Jobs lease/recovery and worker execution tests now use direct
  SQLite/repository setup, leaving the broad app fixture for HTTP/app
  composition tests only.
- Tool-broker evidence and Docker sandbox policy tests now use direct
  SQLite/repository setup with an explicit minimal platform object for
  evidence creation.
- A general architecture guardrail now fails when a non-HTTP test uses
  `ControlPlaneFixture`, preventing this fixture-coupling risk from recurring.
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
- Local Semgrep coverage now checks four product-specific risks: shell
  execution outside policy/sandbox, `shell=True`, plaintext secret persistence,
  and optional runtime adapter bypasses.
- Runtime adapter smoke now emits explicit skip reasons when OpenHands or
  SWE-agent CLIs are not installed and no `AIDO_*_SMOKE_ARGV_JSON` override is
  supplied, so optional-adapter validation is auditable instead of silently
  skipped.
- Runtime adapter release validation now has an explicit preflight mode for
  OpenHands/SWE-agent argv and issue text, so misconfigured issue-to-patch
  validation fails before server startup with an adapter-level JSON report.
- Runtime adapter preflight and release smoke scripts now write ignored
  `.tmp/runtime-validation/*.json` evidence reports when invoked through the
  package scripts, so external adapter validation leaves a reproducible
  artifact without committing runtime output.
- `smoke:runtime:release` now runs the strict release preflight before any
  server-dependent runtime smoke, preventing missing OpenHands/SWE-agent
  configuration from being hidden by server availability errors.
- Stable prompt, IDE connection, MCP server, integration, retrieval search, and
  retrieval reindex records now have generated OpenAPI DTOs instead of
  front-end `JsonObject` fallbacks.
- Skills, overview security posture, Open Design status, and runtime provider
  status now have generated OpenAPI DTOs. The frontend consumes the generated
  runtime provider contract instead of a hand-written duplicate shape.
- Artifact cleanup and artifact retention responses now expose generated DTOs
  for physical artifact files, expired artifacts, and audited retention action
  results while keeping extension metadata flexible.
- Sandbox status, external telemetry status, CLI adapter status, and workspace
  archive evidence packages now expose generated DTOs instead of generic
  operational objects.
- The OpenAPI client generator now preserves nullable fields as `null` instead
  of widening them to `JsonValue`, and project/job mutation responses type
  audit events and permission grants with their domain DTOs.
- Frontend retrieval status and artifact preview surfaces now consume generated
  `RetrievalStatusResponse` and `ArtifactRecord` aliases instead of broad
  `Dictionary` rows.
- Frontend policy revision drawers now consume the generated
  `PolicyRevisionRecord` alias instead of generic `Dictionary` state, keeping
  sandbox policy diffs tied to the OpenAPI contract.
- The central React control-plane hook now keeps retrieval status typed as
  `RetrievalStatusResponse`, so stable backend posture does not widen back to
  an unstructured frontend object.
- Frontend mutating helpers now accept generated `OperationRequestBody` shapes
  and infer generated responses instead of returning untyped `Dictionary`
  payloads for stable v1 operations.
- Web TypeScript typechecking is now an explicit script using Vite-compatible
  module resolution, and event records expose a backend `severity` field for
  typed operational ledgers.
- Governance now has dashboard filters and a strict risk status update form
  backed by the typed risk PATCH endpoint, closing the basic filtering/editing
  UX gap without adding raw JSON editing.

## Next Backend Work

1. Keep `tests_py/control_plane_fixture.py` reserved for FastAPI middleware,
   handshake, and app-composition tests; the new architecture guardrail now
   blocks non-HTTP regressions automatically.
2. Run the strict installed-runtime issue-to-patch smoke on a release
   validation runner with OpenHands/SWE-agent installed. There is no GitHub
   quality workflow now; use `pnpm run smoke:runtime:release:preflight` and
   explicit environment variables on that runner, then run
   `pnpm run smoke:runtime:release` and attach the generated
   `.tmp/runtime-validation/release-preflight.json` and
   `.tmp/runtime-validation/release.json` reports to the release evidence.

## Next Frontend Work

1. Keep only intentional extension payloads flexible: adapter-specific
   metadata/config, pipeline stage payloads, policy rules, tool-call payloads,
   and raw evidence test payload arrays. Convert any new stable operational row
   to a DTO before exposing it to the frontend.
