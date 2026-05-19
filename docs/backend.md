# Backend Architecture

The backend is a Windows-native Python/FastAPI control plane organized around
vertical slices. SQLite is the canonical operational store. Rebuildable indexes,
frontend bundles, caches, and dependency folders are not source of truth.

## Active Slices

- `control_plane`: operational overview read model.
- `projects`: project records and discovery reports.
- `jobs_approvals`: jobs, runs, leases, action requests, and approvals.
- `workflows`: workflow definitions, runs, steps, edges, and events.
- `security_policy`: command classification, deterministic policy decisions,
  permission records, sandbox posture, and secret boundaries.
- `workspaces_projects`: task-scoped workspace allocation, Git worktree support,
  archive lifecycle, and safe degradation when a directory is not a Git repo.
- `agents`: agent profiles, runtime modes, internal mock runtime, model
  policies, model calls, cost usage, skills, model-gateway budget checks, and
  the tool broker.
- `memory_retrieval`: SQLite memory metadata plus rebuildable NumPy/FAISS
  retrieval indexes.
- `evidence`: evidence packages, test results, QA verdict gates, artifacts.
- `governance`: architecture decisions, risk register, and actionable next
  steps.
- `sessions_chats`: v1 session and chat read models.
- `pipelines`: v1 pipeline read models.
- `integrations`: IDE connection contracts and integration adapter surfaces.
- `prompts`: prompt templates and append-only prompt version records.

## Runtime Boundary

The product store facade has been removed. FastAPI and CLI bootstrap now use
`local_control_center/control_plane/runtime.py`, which owns only cwd, DB
connection, schema initialization, loopback token, and runtime project creation.
SQLite connection setup now lives in
`local_control_center/shared/db.py`; additive schema bootstrap lives in
`local_control_center/shared/migrations.py`; shared time and JSON/hash helpers
live in `local_control_center/shared/time.py` and
`local_control_center/shared/serialization.py`. Project catalog operations have
moved to `projects.repository`, IDE connection operations have moved to
`integrations.repository`, and prompt template/version operations have moved to
`prompts.repository`. Memory creation, listing, embedding persistence, and
retrieval rebuild/search now compose `MemoryRepository`, `EventBus`, and
`RetrievalIndex` directly from the memory/retrieval slice instead of calling
through a store facade. Project/catalog HTTP routes now live in
`projects.api`/`projects.commands`, and the concurrent worker opens SQLite and
uses `JobsRepository` directly. Slice routers must use `EventBus` for events and
audit records; guardrail tests reject `platform.record_event`,
`platform.record_audit`, and `platform.get_project` inside slice APIs. The
gated agents planner records proposed actions through `JobsRepository`. The
target is not a larger store object; it is smaller slice-owned SQL with
transaction helpers in `local_control_center/shared`, plus a runtime object that
does not expose domain facade methods.
Operational events and audit records now go through
`local_control_center/shared/event_bus.py`. Product code must not import
`local_control_center.store`; architecture tests reject the file if it returns.
Tests use `tests_py/control_plane_fixture.py`, a repository composition helper,
instead of a product-like store harness.

## Telemetry

`local_control_center/shared/telemetry.py` provides local-first trace events
without requiring Jaeger, Prometheus, or any network exporter. It records:

- `telemetry.http.request` for FastAPI v1 requests with method, path, status,
  duration, and correlation id;
- `telemetry.policy.decision` when permission decisions are persisted;
- `telemetry.tool.call` when the tool broker records a tool call;
- `telemetry.model.call` when the model gateway or internal mock runtime records
  a model call;
- `agent.run.<status>` when agent runs complete.

Telemetry payloads are redacted before persistence and do not include request
headers, loopback tokens, API keys, or bearer values. `X-Correlation-ID` and
`X-Request-ID` are honored when present; otherwise the backend generates a local
correlation id and returns it in the response header.

External OpenTelemetry export is optional and disabled by default. The runtime
continues to work without any OTEL package installed. To enable OTLP/HTTP traces
and metrics, install the optional Python extra and set environment variables
before starting FastAPI:

```powershell
uv sync --extra otel
$env:AIDO_OTEL_EXPORTER = "otlp_http"
$env:AIDO_OTEL_ENDPOINT = "http://127.0.0.1:4318"
$env:AIDO_SERVICE_NAME = "aido-local-control-center"
```

`AIDO_OTEL_TRACES_ENDPOINT`, `AIDO_OTEL_METRICS_ENDPOINT`, and
`AIDO_OTEL_HEADERS` can override the defaults. Export failures are recorded in
telemetry status and must not break local operation. The current status is
available at `GET /api/v1/telemetry/status`.

## API Composition

`local_control_center.api.create_app()` is the only FastAPI composition path.
Routers are mounted from vertical slices. Removed compatibility routes are not
mounted.

Mutating endpoints require the loopback token from
`GET /api/v1/security/handshake`. Tool calls and shell execution must pass:

```text
tool broker -> policy engine -> approval/sandbox decision -> evidence/audit
```

Allowed shell execution is intentionally narrower than policy approval:

- policy must return `allow`;
- the tool call must request `execute: true`;
- the command must include a structured `argv` list;
- execution uses `RestrictedSubprocessSandbox` with `shell=False` or the Docker
  sandbox adapter;
- restricted subprocess executables must be allowlisted;
- Docker images must come from the local catalog and run with `--network none`
  unless the active sandbox profile explicitly allowlists another mode;
- the working directory must stay inside the allocated workspace.

String-based shell execution is not a supported fallback.

Workflow details now include linked workspaces, evidence packages, jobs, and
agent runs through explicit `workflow_run_id`/`workflow_step_id` references.
This avoids treating workflows as a decorative graph detached from execution.

Integrations are registry-first. MCP servers can be registered and audited.
MCP, OpenHands, and SWE-agent can execute only as runtime adapters invoked by
`ToolBroker` after the agent profile allows the tool and the policy engine
returns `allow` or a one-use grant has been consumed. MCP read-only discovery
operations such as `tools/list` can run after policy allow; MCP tool execution
and sensitive OpenHands/SWE-agent commands require approval. Adapter output is
persisted as `agent_tool_calls` and feeds evidence generation just like shell or
Docker execution.

Workspace archive captures a lightweight evidence snapshot of non-ignored
workspace files. The snapshot is stored as a diff reference in an evidence
package so QA can inspect what existed at archive time even when the project is
not a Git repository.

If the workspace is a Git worktree, archive also captures bounded Git status,
name-only, stat, and patch evidence before cleanup. Large patches are promoted
to artifact files with hashes and referenced from the evidence package instead
of being stored inline. This closes the gap where worktree removal could
otherwise erase implementation evidence before QA review.

Docker sandbox support is optional and never required for startup. The adapter
can execute a catalog image with structured argv and locked-down defaults only
through the agent tool broker. Image catalog, network allowlist, memory, CPU,
and timeout come from `sandbox_profiles`, not tool-broker constants. Low-risk
calls require a policy `allow` decision. Sensitive calls require a one-use
`permission_grant` produced by approving the matching action request with a
non-empty reason. The grant is validated against project, job, agent, tool,
command, and path, then consumed before execution. Execution results are
redacted, attached to agent tool calls, and summarized in an evidence package
referenced by the agent run. Large stdout/stderr are promoted to
`execution_log` artifacts before `agent_tool_calls` is persisted, so SQLite
keeps artifact references and hashes instead of unbounded execution logs.

Evidence API payloads must not become an unbounded data lake. Large logs and
base64 screenshots are promoted to artifact files with hashes and referenced
from the package JSON. This keeps SQLite rows queryable and the dashboard
responsive.

Artifact reads are authenticated and confined to the local artifact root. The
API verifies package ownership and SHA-256 before serving the file, which blocks
path traversal through malicious artifact rows.

## Migration Policy

Migrations are additive. SQLite tables are not dropped automatically. FAISS is
optional and treated as a rebuildable index; NumPy fallback remains supported.
Schema creation and migration seeds are centralized in
`local_control_center/shared/migrations.py`; slice repositories own operational
reads/writes after tables exist.

Current migration versions:

- v1: baseline platform schema.
- v2: workflows, policy decisions, evidence, agents, model policies.
- v3: workspaces, skills, artifacts, test results, QA verdicts, model providers.
- v4: workflow traceability columns for allocated workspaces.
- v5: architecture decisions, risk register, and next steps.
- v6: workflow traceability columns for jobs and agent runs.
- v7: integrations, MCP server registry, and MCP tool-call records.
- v8: permission grants for one-use approval-to-execution correlation.
- v9: sandbox profiles for Docker image catalogs, network modes, and resource
  limits.
- v10: revocation metadata for permission grants and sandbox profiles.
- Sandbox profile updates are routed through a token-protected, reason-required
  API endpoint; direct SQLite edits are no longer the operational path.
