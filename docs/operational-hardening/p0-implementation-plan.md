# AIDO P0 Operational Hardening - Implementation Plan

> Status: approved for execution by the operator request dated 2026-09-04.
>
> Source baseline: `origin/dev` at `94eaf8c1756f17493c7f80af359d86cb9561816c`.
>
> Working branch: `codex/aido-operational-hardening-p0`.

## Verifiable objective

Keep the FastAPI control plane responsive and recoverable while every productive long-running
operation is admitted by host capacity, executed by a separate durable worker, contained in an
operating-system process tree, cancellable, and represented by incremental SQLite state and evidence.
P0 is complete only when `quality:pr` and `scripts/verify-operational-hardening.ps1` both exit zero.

## Baseline inventory and risk classification

The inventory was performed before code changes. The baseline worktree was clean, `HEAD` matched
`origin/dev`, and the host had sufficient capacity for inspection. Heavy validation remains deferred
until the final sequential gate.

| Surface | Baseline use | Classification | P0 risk and treatment |
| --- | --- | --- | --- |
| `api.py` | FastAPI lifespan starts `LocalWorkerRuntime`, stores it in `app.state`, shares `platform.connection`, and wraps `/api/*` in a global mutex | `background_scheduler`, `short_database_operation` | Worker failure is coupled to API; long calls serialize the entire API. Remove in-process worker and mutex; inject operation-scoped connections. |
| `cli.py` | Starts `ConcurrentWorker` in a daemon thread when API and worker are requested together | `background_scheduler` | No OS isolation or durable leadership. Replace combined mode with explicit API/worker entrypoints and a local OS-process supervisor. |
| `workers/runtime.py` | Daemon polling thread and in-memory pause/run state | `background_scheduler` | State disappears on crash and two workers can run. Replace control/status with SQLite state, heartbeat, leader lease, and fencing. |
| `jobs_approvals/worker.py` | `ThreadPoolExecutor` executes Product Loop/research synchronously | `productive_process` orchestration | Fixed job leases can expire while an old owner continues. Add resource admission, lease heartbeat, fencing checks, and cancellation watcher; default concurrency becomes one. |
| `agents/ai_execution.py` | `ThreadPoolExecutor` performs provider branches; HTTP calls occur outside short quota transactions | `productive_remote_call` | HTTP request waits for providers and parallelism is provider-local rather than host-global. Persist an execution first, then dispatch from the worker under resource leases. |
| Agent/workflow/Product Loop API routes | Direct calls to agent runners, model providers, QA, issue-to-patch/PR, and Product Loop coordinators | `productive_remote_call`, `productive_process` | Long work runs on HTTP request threads and shares the request connection. Convert productive routes to durable `202 queued` executions. |
| Provider adapters and research/n8n/GitHub helpers | Bounded `urllib` requests to LLM/provider/search/integration endpoints | `productive_remote_call` | Must execute outside transactions and under execution cancellation/resource accounting. Keep short health probes synchronous only when allowlisted and cached. |
| `integrations/mcp_gateway.py` | Ephemeral MCP stdio process plus two reader threads | `productive_process` | Process tree is not centrally owned and cancellation is local. Route process creation through `ProcessSupervisor`. |
| `agents/cli_session_stream.py` | Background session thread plus subprocess reader threads and in-memory handle map | `productive_process`, `background_scheduler` | Cancellation is process-local and not crash-recoverable. Reuse durable executions, process rows, and the supervisor; preserve streaming events. |
| `security_policy/sandbox.py` | `Popen` for restricted commands; reader threads; direct Docker `subprocess.run`; `taskkill` tree cleanup | `productive_process` | Several execution paths bypass common resource/process evidence. Preserve the public sandbox facade but delegate productive launch/wait/terminate to `ProcessSupervisor`. |
| `security_policy/sandbox.py` version/auth helpers | Exact allowlisted commands with 5-10 second timeouts | `read_only_probe` | May remain synchronous, cached, and outside the governor; keep the allowlist explicit. |
| `nvidia_nim/system_probe.py` | Exact `wsl`, `nvidia-smi`, Docker/Podman and toolkit information commands | `read_only_probe` | Reuse for NVIDIA facts and keep bounded. Do not create a competing GPU detector. |
| `security_policy/git_command_runner.py` | Structured `git` argv | `productive_process` or `read_only_probe` by verb | Centralize mutating/expensive Git commands; explicitly allowlist cheap reads. |
| `start_control_center.py` and `start-control-center.ps1` | Build only when the static artifact is absent; launch one combined Python runtime | `productive_process` | Preserve missing-artifact build policy; replace the combined runtime with API and worker child processes. |
| Release validation scripts | Direct bounded runtime validation commands | `productive_process` in release tooling | Route operational validation through the P0 verification lane or document as release-only allowlisted launchers. |
| `scripts/prune-stale-worktrees.py` | Mutating Git cleanup command | `productive_process` | Keep explicit operator scope and route through the supervised process boundary when invoked by product code. |
| QA runners | Pytest, Node, Playwright, Semgrep, Ruff, Git and build commands via ToolBroker/sandbox | `productive_process` | Classify workload before launch; admit globally; serialize heavy combinations; preserve bounded output and artifacts. |
| `shared/db.py` | WAL connection with `check_same_thread=False`, foreign keys and 30-second busy timeout | `short_database_operation` | The flag masks cross-thread sharing. Introduce connection factory/context manager and deterministic close; retain WAL/foreign keys/busy timeout. |
| Product routers and services | 151 direct `platform.connection` uses | `short_database_operation` mixed with productive work | Replace router use with operation-scoped connection dependencies. Bootstrap may retain a separately scoped connection only for migration/startup. |
| Explicit transaction sites | 67 references, with short writes around leases, quotas, FSM updates, settings and histories | `short_database_operation` | Preserve atomic writes but never hold a transaction across provider, subprocess, Git, MCP or network waits. Add a test guard at external boundaries. |
| Test process/thread uses | Local HTTP fixtures, Git fixtures, process-tree fixtures and concurrency assertions | `test_only` | Allowed, with deterministic cleanup and no aggressive parallelism. |

### Confirmed baseline defects

1. The global HTTP mutex includes `await call_next`, so one productive request blocks unrelated API
   reads even though Uvicorn can otherwise schedule them independently.
2. API lifespan and CLI combined mode can start worker execution inside the API process.
3. Worker pause stops new polling only; it does not cancel an active process tree.
4. Worker state and leadership are process-local; job leases have no worker fencing token.
5. The old worker can complete a job after a lease takeover because completion does not verify leader
   fencing and current job ownership.
6. Resource routing concerns model/provider choice, not host CPU/RAM/disk/GPU admission.
7. Productive subprocesses are split among the sandbox, MCP, streaming CLI, Git, scripts and release
   validators, so containment/evidence is inconsistent.
8. Codex profiles and several productive catalog/routing defaults pin `gpt-5.5`; readiness does not
   prove required CLI flags or a validated executable fingerprint.
9. `worker.autostart=true`, `worker.maxConcurrentJobs=2`, and a generic numeric coercion allow unsafe
   values such as 16 workers or negative resource limits.
10. Existing `ai_executions` describe synchronous provider fan-out and omit queued/cancelling/
    cancelled/resource-wait lifecycle states needed for durable HTTP dispatch.

## Affected modules and new boundaries

### Existing modules to refactor

- Bootstrap: `api.py`, `cli.py`, `control_plane/runtime.py`, `workers/api.py`, `workers/runtime.py`.
- Persistence: `shared/db.py`, `shared/migrations.py`, `jobs_approvals/repository.py`.
- Execution: `jobs_approvals/worker.py`, `security_policy/sandbox.py`, `agents/tool_broker.py`,
  `agents/ai_execution.py`, `agents/cli_session_stream.py`, `integrations/mcp_gateway.py`, QA and CLI
  runtime adapters.
- Readiness/routing: `agents/runtime_registry.py`, `agents/runtime_status.py`,
  `agents/cli_runtimes/{base,codex_cli,claude_code_cli}.py`, model catalog/router and ProductOwner
  runtime construction.
- Configuration and UI: `settings/registry.py`, OpenAPI models/client, current status bar/settings and
  an operational surface that follows the existing design system.
- Tooling/docs: package scripts, PowerShell/Node runners, operational verification, architecture docs,
  runbooks and roadmap.

### New packages

- `local_control_center/host_resources/`: models, probes, profiles, repository, governor, retention,
  and API. This is host admission/backpressure, distinct from `AIResourceManager` model routing.
- `local_control_center/process_supervision/`: protocol/models, service, Windows Job Object backend,
  POSIX process-group backend, and process evidence repository.
- `local_control_center/executions/`: durable generic operation contracts, repository, events, API,
  dispatcher helpers and cancellation/control state.

## Additive migrations

Migration phases remain reentrant and preserve existing rows.

1. Worker leadership/control: `worker_leader_leases`, `worker_control_state`, `worker_heartbeats`; add
   `leader_fencing_token` to `job_runs` and a monotonic token source guarded by `BEGIN IMMEDIATE`.
2. Generic executions/events: durable lifecycle, cancellation request/reason, workload class,
   operation kind/payload references, timestamps and incremental events. Existing AI execution rows
   receive compatibility defaults rather than destructive replacement.
3. Host resources: `host_resource_snapshots`, `resource_leases`, `resource_usage_samples`, and
   `resource_violations`, with indexes for active/expired rows and retention scans.
4. Managed processes: process ownership, command fingerprint, root PID, resource/evidence linkage,
   terminal metrics and termination reason. Prompts, secrets and raw command lines are excluded.
5. Codex compatibility: executable fingerprints, capability observations and validated smoke records;
   migrate old model/profile values to semantic aliases without asserting a current model.

Each phase records its schema version only after the complete migration succeeds. Tests run every new
migration twice and exercise upgrade from a baseline database copy.

## New contracts

- Worker: leader lease acquire/renew/release/takeover with monotonically increasing fencing token;
  durable control commands `pause`, `resume`, `drain`, and status `leader`/`standby`/`offline`.
- Execution HTTP: `POST -> 202 {executionId,status:"queued"}`;
  `GET /api/v1/executions/{id}`; incremental `GET .../events`; authenticated `POST .../cancel`.
- Host resources: `ResourceSnapshot`, `WorkloadProfile`, `ResourceAdmissionRequest`,
  `ResourceAdmissionDecision`, `ResourceLease`, `ResourceUsageSample`, `ResourceViolation`.
- Processes: `ProcessSupervisor` with supervised spawn/wait/cancel/emergency-stop/stats/release;
  Windows and POSIX implementations selected in one factory.
- Readiness: installed, version, authenticated, configured, global/project/policy/resource gates,
  health, executable, effective status, blocking reasons and last checked time.
- Codex compatibility: parsed semantic version, required flag/capability evidence, executable
  fingerprint, validated smoke status and semantic model alias resolution.
- Quality: `quality:fast`, `quality:story`, `quality:pr`, `quality:release`; legacy `quality` aliases PR.

## Mandatory implementation sequence

### Phase 1 - RED baseline tests

- Add focused tests that expose the global request lock, API-owned worker, absent durable leader,
  incomplete pause/cancel, missing host admission, pinned Codex model, and decentralized productive
  subprocess creation.
- Run only the new tests and record their expected failures.
- Commit: `Test (Operations): reproduce bloqueos y ausencia de gobierno de recursos`.

### Phase 2 - API/worker process separation and leader fencing

- Add separate CLI entrypoints and local supervisor; remove API lifespan worker startup.
- Add durable worker control, heartbeat, lease acquisition/renewal/takeover and job-run fencing.
- Make worker endpoints SQLite-only and default autostart/concurrency to false/one.
- Verify two independent SQLite connections and takeover behavior.
- Commit: `Refactor (Worker): separa API y ejecución con liderazgo durable`.

### Phase 3 - Operation-scoped SQLite connections

- Add connection factory/context manager and FastAPI dependency; construct repositories per request.
- Remove global HTTP mutex and shared request connection; use independent telemetry writes.
- Add PASSIVE checkpoint, WAL bytes and SQLite version diagnostics plus bounded retention.
- Verify overview responsiveness during a slow injected operation and concurrent worker write.
- Commit: `Refactor (Database): elimina la conexión compartida y el lock HTTP global`.

### Phase 4 - HostResourceGovernor

- Add validated settings/ranges, profiles, psutil host probes and NVIDIA facts reuse.
- Implement durable global admission/resource leases, resource-wait reasons, renewal/recovery, hard-floor
  violation handling and sample retention.
- Integrate admission before job execution and reevaluation in later worker polls.
- Commit: `Feature (Resources): agrega admisión global y backpressure del host`.

### Phase 5 - Process supervision and real cancellation

- Add Windows Job Object and POSIX process-group backends behind `ProcessSupervisor`.
- Delegate productive sandbox, CLI streaming, MCP and QA/tool launches to the supervisor.
- Persist bounded output, spill artifacts, hash, resource/process metrics and terminal reasons.
- Add control watcher, graceful/forced cancellation, drain and scoped idempotent emergency stop.
- Add architecture guardrail for raw productive subprocess creation.
- Commit: `Feature (Processes): supervisa y cancela árboles de ejecución`.

### Phase 6 - Durable asynchronous execution and effective readiness

- Queue long HTTP operations as generic executions and return 202 immediately; execute in worker.
- Expose execution state/events/cancel endpoints and stop new stages after cancellation.
- Prove Codex compatibility using version/help capabilities and executable fingerprint; fail closed when
  unvalidated. Resolve semantic aliases from the current enabled model catalog and migrate old rows.
- Expose effective readiness with every independent gate and blocking reason.
- Commit: `Refactor (Runtime): hace asincrónica la ejecución y unifica readiness`.

### Phase 7 - Quality tiers, evidence and minimal operational UI

- Implement sequential quality scripts and workload classifications; no xdist/parallel heavy gates.
- Complete process/resource evidence and operational APIs.
- Add existing-design-system UI for API/worker/leader/control/resource/process/readiness truth, generated
  OpenAPI DTOs and bilingual i18n. Preserve keyboard access, focus, responsive overflow and reduced
  motion. No productive state is inferred in React.
- Commit: `Feature (Operations): expone recursos, procesos y controles seguros`.

### Phase 8 - Hardening, recovery and documentation

- Exercise migrations, crash recovery, fault injection, retention, cleanup and architecture guards.
- Regenerate OpenAPI and document architecture, runbook, recovery, profiles, rollback and verification.
- Add the P1/P2 backlog only as gated documentation.
- Commit: `Docs (Operations): documenta el runtime operacional P0`.

## Rollback strategy

1. Stop the AIDO worker first and allow/force only its managed process trees to terminate; keep API
   available for inspection.
2. Back up the SQLite database plus `-wal`/`-shm` consistently using SQLite backup/checkpoint tooling,
   never by copying a live partial file set.
3. Revert local commits in reverse phase order. Additive tables/columns are left in place by default;
   old code ignores them. No destructive down migration runs automatically.
4. The startup supervisor accepts API-only mode, so a bad worker/runtime phase can be isolated without
   losing control-plane access.
5. Release resource leases and mark managed-process rows terminal only after checking ownership and
   fencing. Never terminate a PID not recorded as an active AIDO-managed process with a matching
   identity.
6. Restore from the pre-upgrade backup only after stopping API and worker and retaining the failed
   database as evidence.

## Test matrix by phase

| Phase | Focused evidence required before commit |
| --- | --- |
| 1 | New defect tests fail for the real baseline reasons. |
| 2 | Two-worker contention, single leader, heartbeat expiry/takeover, fencing rejection, API-only bootstrap and durable worker controls. |
| 3 | Slow operation does not delay health/overview/worker status; concurrent SQLite writer; PASSIVE checkpoint; no external wait in transaction; deterministic close. |
| 4 | Soft/hard memory floors, Unreal/local-GPU conflict, build/browser exclusion, two/three remote calls, expired lease recovery, redacted snapshot and retention. |
| 5 | Normal exit, parent+child timeout/cancel, scoped emergency stop, no orphan, bounded output/artifacts, peak memory/CPU/exit code and platform backend tests. |
| 6 | 202 queued lifecycle/events/cancel; Codex legacy/current/future/flag/auth cases; alias resolution/no-model block; no prompt/credential persistence; effective readiness reasons. |
| 7 | Quality tier selection/no aggressive parallelism; OpenAPI DTO use; UI controls/status/a11y/responsive focused Playwright; complete evidence fields. |
| 8 | Double migration, upgrade/backup/restore, abrupt recovery, retention, architecture guards, OpenAPI drift, documentation and full sequential verification. |

## Technical risks and controls

- **Windows suspended launch:** Python/pywin32 handle inheritance and Uvicorn environments can differ.
  Keep all pywin32 imports in `windows_job.py`, close handles deterministically, and test real Windows
  containment separately from fake-backend unit tests.
- **PID reuse:** Process ownership cannot rely on PID alone. Persist start time/fingerprint and verify
  current process identity before emergency termination.
- **SQLite contention:** More connections remove the Python mutex but increase real write contention.
  Keep transactions small, add targeted indexes/retries via busy timeout, and observe WAL/checkpoints.
- **Stale leaders:** Lease expiry alone is insufficient. Every claim/renew/complete path must check the
  current fencing token and owner in the same transaction.
- **Cancellation races:** Terminal completion and cancellation can race. Use guarded state transitions;
  cancellation wins once `cancel_requested_at` is persisted unless work had already committed terminally.
- **Resource measurements:** Host counters are samples, not hard guarantees. Combine admission with OS
  job limits and report unsupported commit/GPU metrics as unavailable rather than zero.
- **Compatibility drift:** A future Codex version is validation-required until its required flags and a
  smoke record for that executable fingerprint are proven; no optimistic version range.
- **Legacy API clients:** Keep existing read contracts and add compatibility fields; long write routes
  receive explicit 202 contracts and migration notes rather than silent response-shape changes.
- **Gate cost:** Full Python/web/Playwright/Semgrep is intentionally final and sequential. Focused tests
  and story tiers provide iteration feedback without competing with user workloads.

## Definition of done

- API and worker run as independent OS processes; worker crash does not stop API.
- Exactly one valid worker leader exists, with heartbeat, takeover and monotonic fencing.
- No global mutex wraps HTTP requests and no long request uses a shared platform connection.
- Long productive routes return a durable queued execution and remain observable/cancellable.
- HostResourceGovernor enforces workload coexistence, soft wait and hard-floor response.
- Every productive child process is contained by a Job Object on Windows or process group on POSIX,
  has bounded output/evidence and can be terminated with descendants.
- Pause, resume, cancel, drain and emergency stop have distinct durable semantics and audit events.
- Codex readiness is capability/fingerprint based; semantic aliases resolve against real enabled models;
  productive profiles contain no obsolete fixed model IDs.
- Minimal UI consumes generated OpenAPI DTOs and presents backend truth accessibly.
- New migrations are additive/reentrant and crash/upgrade/backup/restore paths are verified.
- `git diff --check`, focused tests, OpenAPI drift, `quality:pr` and the operational verification script
  all exit zero, with no secrets and no unmanaged orphan processes.
- No P1/P2 integration is implemented, pushed or merged.

@author Rodrigo Mason
