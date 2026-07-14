# NVIDIA NIM local runtime and configuration UI implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task by task. Backend tasks use `superpowers:test-driven-development`; frontend tasks also follow `architecture-web`. `ui-ux-pro-max` is unavailable in this runtime, so preserve AIDO's existing interaction/design-system patterns and verify WCAG 2.2 AA behavior. Do not commit or push.

**Goal:** Give operators a complete, honest NVIDIA setup surface for multiple hosted/free/partner endpoints, editable enforced limits, multi-model execution, and recoverable local-NIM preflight/lifecycle without mutating incompatible or shared infrastructure.

**Architecture:** Hosted endpoint configuration remains part of the existing provider-account and Model Gateway surfaces. Persistent local-NIM lifecycle is a separate operational adapter behind policy/approval and ToolBroker; it never occurs as a side effect of saving an account. The server owns remote state and exposes typed OpenAPI contracts; React owns only local form/dialog state and re-fetches authoritative data after mutations.

**Tech stack:** Python 3.12, FastAPI, SQLite, Pydantic v2, existing ToolBroker/remediation/event boundaries, React 19, TypeScript, Vite, generated OpenAPI client, Playwright, Biome.

**Depends on:** `docs/superpowers/plans/2026-07-13-nvidia-nim-control-plane.md` Tasks 1, 2, 4, 6, and 7.

## Global constraints

- Do not commit, push, stage, reset, or overwrite unrelated dirty-worktree changes.
- Use `apply_patch` for edits and inspect existing diffs before touching `remediations/service.py`, `RuntimeSetupPanel.tsx`, `CommandPalette.tsx`, `tsconfig.json`, or generated files.
- Use `corepack pnpm@10.24.0`; never use `npm` or `npx`.
- Store/fetch remote data through existing client functions; do not create a second global frontend store.
- Keep secrets out of React state after submission where possible; never render or return resolved values.
- Trial endpoints are evaluation-only; UI labels must not imply an SLA, stable free allowance, or production readiness.
- The current host is not authorized for local NIM mutation. A read-only preflight must block before image pull, package install, port bind, or WSL change.
- Never silently modify the shared `Ubuntu-24.04-bot` distro, `.wslconfig`, Docker Desktop, k3s, Podman, drivers, toolkit, CDI, or firewall.
- `start` and `stop` are explicit auditable actions; provider create/update, health reads, and page loads are side-effect free.
- Do not use `DockerSandbox` for persistent NIM lifecycle. It is ephemeral and lacks the required GPU/CDI contract.
- No new frontend dependency unless existing primitives cannot implement an accessible control.
- Every user-visible string goes through the existing i18n catalog.

## Task 1: Persist local deployment definitions and preflight evidence

**Files:**

- Add: `local_control_center/nvidia_nim/__init__.py`
- Add: `local_control_center/nvidia_nim/contracts.py`
- Add: `local_control_center/nvidia_nim/repository.py`
- Add: `local_control_center/nvidia_nim/preflight.py`
- Modify: `local_control_center/shared/migrations.py`
- Add: `tests_py/test_nvidia_nim_local_deployment.py`

### 1.1 Write failing repository and pure-preflight tests

- [ ] Test a deployment definition with endpoint id, NIM/model/profile, image ref, port, cache path, runtime target, distinct NGC/Hugging Face pull credential refs, optional inference credential ref, terms acceptance evidence, status, and timestamps. Never reuse a pull credential as an HTTP bearer token.
- [ ] Test that resolved secret values are rejected by contracts and absent from repository serialization.
- [ ] Inject a system-probe port so tests do not shell out. Cover GPU architecture/VRAM, driver, RAM/swap, disk, runtime, toolkit/CDI, port, credential-reference status, and terms.
- [ ] Encode current-host evidence as a fixture and prove Qwen-Image-Edit is blocked before any mutation with:

```python
assert result.status == "blocked_incompatible_hardware"
assert result.required_gpu_memory_gib == 80
assert result.mutation_attempted is False
```

- [ ] Prove unsupported NVIDIA WSL/GPU/runtime combinations fail closed even when raw VRAM appears sufficient.
- [ ] Prove missing toolkit, CDI, credential, terms, disk, or free port emits a stable reason and one or more recoverable actions.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_nvidia_nim_local_deployment.py -q
```

### 1.2 Add additive local-runtime schema

- [ ] Add local-runtime tables in additive phase 57; do not reopen the already-applied provider/capability/quota/pricing/execution phases 52–56.
- [ ] Create normalized `nvidia_nim_deployments` and append-only `nvidia_nim_preflight_runs` tables.
- [ ] Store official profile requirements with source URL and `verified_at`; do not hardcode them as timeless truth without provenance.
- [ ] Store sanitized probe observations and remediation ids, not command output blobs or environment secrets.
- [ ] Keep provider account identity linked by endpoint id while allowing a deployment definition to exist in blocked state.

### 1.3 Implement deterministic preflight

- [ ] Separate pure evaluation from OS probes:

```python
facts = system_probe.collect(target)
profile = profile_repository.get(request.profile_id)
decision = preflight.evaluate(facts=facts, profile=profile, terms=request.terms)
```

- [ ] Put timeouts and allowlists around probes. Redact all subprocess stderr/stdout before persistence.
- [ ] Return all independent blockers in one result so recovery does not become a one-error-at-a-time loop.
- [ ] Never pull an image or execute a generation call during preflight.

### 1.4 Verify

```powershell
uv run pytest tests_py/test_nvidia_nim_local_deployment.py -q
uv run ruff check local_control_center/nvidia_nim local_control_center/shared/migrations.py tests_py/test_nvidia_nim_local_deployment.py
git diff --check
```

## Task 2: Add brokered NIM lifecycle with fail-closed approval

**Files:**

- Add: `local_control_center/nvidia_nim/broker_adapter.py`
- Add: `local_control_center/nvidia_nim/service.py`
- Modify: `local_control_center/agents/tool_broker.py`
- Modify: `local_control_center/security_policy/policy_engine.py`
- Modify: `local_control_center/jobs_approvals/worker.py`
- Modify: `local_control_center/jobs_approvals/repository.py`
- Add: `tests_py/test_nvidia_nim_local_broker.py`
- Regression: `tests_py/test_workspace_isolation_contract.py`
- Regression: `tests_py/test_local_worker_runtime.py`

### 2.1 Write failing no-mutation and lifecycle tests

- [ ] Inject a fake process/container command boundary and prove `start` is never invoked when preflight is blocked, approval missing/expired, or runtime target is not explicitly selected.
- [ ] Prove image refs, ports, mounts, cache roots, runtime executable, and GPU/CDI flags come from allowlisted typed values rather than concatenated shell strings.
- [ ] Prove registry credentials use stdin/an ephemeral secret channel and never command arguments, logs, job payloads, or persisted audit metadata.
- [ ] Prove repeated `start` with the same idempotency key does not launch a duplicate container.
- [ ] Prove health/inspect is read-only, `stop` targets only the persisted deployment identity, and neither action can reach unrelated k3s/Podman containers.
- [ ] Prove a stale worker cannot overwrite a replacement worker's final state after lease expiry.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_nvidia_nim_local_broker.py -q
```

### 2.2 Implement a dedicated operational adapter

- [ ] Define typed operations `preflight`, `start`, `stop`, and `inspect`; only `preflight`/`inspect` are read-only.
- [ ] Require persisted approved jobs for `start`/`stop` and re-run preflight immediately before mutation to close the TOCTOU gap.
- [ ] Generate an argument array directly for the explicitly supported runtime. Do not execute shell text.
- [ ] Use an isolated, explicitly selected runtime target. If only the shared bot_v2 distro is available, return `local_runtime_not_ready` plus a remediation; do not install into it.
- [ ] Update provider/runtime truth only after the documented health endpoint succeeds. A running container without healthy NIM remains degraded/unavailable.
- [ ] Stop/cleanup only resources labelled with the exact AIDO deployment id.

### 2.3 Strengthen worker lease finalization

- [ ] Add compare-and-set ownership/version checks so a worker whose lease expired cannot complete or fail a requeued job.
- [ ] Add worker support for the NIM lifecycle job kind without bypassing ToolBroker/policy.
- [ ] Keep network/container work outside SQLite transactions.

### 2.4 Verify

```powershell
uv run pytest tests_py/test_nvidia_nim_local_broker.py tests_py/test_workspace_isolation_contract.py tests_py/test_local_worker_runtime.py -q
uv run ruff check local_control_center/nvidia_nim local_control_center/agents/tool_broker.py local_control_center/jobs_approvals local_control_center/security_policy/policy_engine.py
git diff --check
```

## Task 3: Expose local-NIM API and persisted remediations

**Files:**

- Add: `local_control_center/nvidia_nim/api.py`
- Modify: `local_control_center/api.py`
- Modify carefully: `local_control_center/remediations/contracts.py`
- Modify carefully: `local_control_center/remediations/service.py`
- Modify: `local_control_center/i18n/default_catalog.json`
- Add: `tests_py/test_nvidia_nim_local_api.py`
- Regression: `tests_py/test_remediation_blocker_experience.py`
- Regression: `tests_py/test_ci_and_openapi_client.py`

### 3.1 Write failing API/error tests

- [ ] Cover list/create/update deployment definitions and read-only preflight/inspect.
- [ ] Cover explicit approved start/stop workflow, idempotency, and `202` job response.
- [ ] Assert status semantics: `404` missing deployment, `409` policy/resource/compatibility conflict, `422` malformed definition, sanitized `500` only for unexpected internal errors.
- [ ] Assert incompatible hardware returns a persisted remediation with exact observed and required resources, documentation source, and an action that cannot mutate until separately approved.
- [ ] Assert raw environment/process/provider error bodies never reach the response.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_nvidia_nim_local_api.py tests_py/test_remediation_blocker_experience.py -q
```

### 3.2 Implement REST resources under `/api/v1/nvidia-nim`

- [ ] Add endpoints for deployments, preflight runs, start/stop job creation, and inspect.
- [ ] Keep account CRUD in the provider API; the NIM API references endpoint ids and handles only operational lifecycle.
- [ ] Require normal write authorization and existing approval/policy mechanisms.
- [ ] Audit mutations and blocker/remediation creation with allowlisted payloads.

### 3.3 Add specific remediation contracts

- [ ] Map `blocked_incompatible_hardware`, missing toolkit/CDI, missing credential ref, terms not accepted, port conflict, and isolated runtime missing.
- [ ] A remediation may open the correct settings/docs or prepare an approval request; it may not silently install drivers/toolkit, resize WSL, accept terms, or spend money.
- [ ] Keep user-visible copy in i18n with explicit evaluation/production distinction.

### 3.4 Regenerate and verify API

```powershell
corepack pnpm@10.24.0 run openapi:generate
uv run pytest tests_py/test_nvidia_nim_local_api.py tests_py/test_remediation_blocker_experience.py tests_py/test_ci_and_openapi_client.py -q
uv run ruff check local_control_center/nvidia_nim local_control_center/api.py local_control_center/remediations
git diff --check
```

## Task 4: Make the provider wizard endpoint-scoped

**Files:**

- Modify carefully: `local-control-center/web/src/features/runtime-setup/AddProviderWizard.tsx`
- Modify: `local-control-center/web/src/features/runtime-setup/runtimeSetup.ts`
- Add: `local-control-center/web/src/features/runtime-setup/NvidiaNimEndpointFields.tsx`
- Add: `local-control-center/web/src/features/runtime-setup/nvidiaNimEndpoints.ts`
- Modify: `local-control-center/web/src/api/client.ts`
- Modify: `local_control_center/i18n/default_catalog.json`
- Add: `tests_web/nvidia-nim-endpoints.spec.js`
- Regression: `tests_web/settings-providers.spec.js`

### 4.1 Write failing Playwright contract tests

- [ ] Mock only the HTTP boundary and prove the wizard can create two NVIDIA accounts with different `instanceId`, deployment mode, API family, URL, credential ref, and terms/pricing mode.
- [ ] Assert instance-id collision and malformed URL errors are announced accessibly and preserve form input.
- [ ] Assert hosted trial displays an evaluation-only warning and partner/enterprise modes do not falsely claim free access.
- [ ] Assert a credential value is not echoed after save; only its reference/status is shown.
- [ ] Assert sync/validate actions target the endpoint instance id while catalog identity remains `nvidia_nim`.

Run and confirm RED:

```powershell
corepack pnpm@10.24.0 exec playwright test tests_web/nvidia-nim-endpoints.spec.js --config=playwright.config.mjs --project=desktop
```

### 4.2 Implement a focused NVIDIA subform

- [ ] Preserve the general wizard and render NVIDIA-only fields through a small composed component.
- [ ] Use controlled local form state; on success invalidate/re-fetch server data rather than patching multiple client caches manually.
- [ ] Validate endpoint id and absolute HTTP(S) URL client-side for feedback, while treating server validation as authoritative.
- [ ] Offer deployment modes `hosted_trial`, `self_hosted_development`, `self_hosted_enterprise`, and `partner_paid`; filter API families by actual supported backend adapters.
- [ ] Do not auto-generate one shared credential ref from catalog id when multiple endpoints need isolation.
- [ ] Keep focus in the error summary on failure and move focus to the new endpoint row on success.

### 4.3 Verify

```powershell
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 exec playwright test tests_web/nvidia-nim-endpoints.spec.js tests_web/settings-providers.spec.js --config=playwright.config.mjs --project=desktop
corepack pnpm@10.24.0 run lint:web
git diff --check
```

## Task 5: Add editable limits and effective-state UI

**Files:**

- Modify: `local-control-center/web/src/features/model-gateway/ProviderLimitsPanel.tsx`
- Modify: `local-control-center/web/src/features/model-gateway/useModelGatewayTabData.ts`
- Modify: `local-control-center/web/src/api/client.ts`
- Modify: `local_control_center/i18n/default_catalog.json`
- Extend: `tests_web/nvidia-nim-endpoints.spec.js`

### 5.1 Write failing UI tests

- [ ] Prove an operator can edit RPM, TPM, daily/monthly requests/tokens, known-cost budget, `maxConcurrency`, timezone, and fallback retry delay.
- [ ] Prove negative/ambiguous values are rejected and zero semantics are explicitly labelled rather than guessed.
- [ ] Prove operational state is read-only: committed/reserved usage, active leases, cooldown, observed rate limit, and reset timestamps.
- [ ] Prove unknown usage/cost renders as “no informado”/unknown, never `$0` or `0 tokens`.
- [ ] Prove save failure preserves inputs, announces the error, and does not optimistically display unapplied limits.
- [ ] Prove keyboard and screen-reader users can associate labels, units, help text, errors, and save status.

### 5.2 Implement policy/state separation

- [ ] Present endpoint/model scope and configured policy in an editable form.
- [ ] Present current window/reservation/cooldown evidence in a separate read-only region with `aria-live` only for meaningful refresh changes.
- [ ] Fetch effective limit state when the budgets tab is opened and re-fetch after a successful mutation.
- [ ] Use ISO timestamps from the server and existing locale formatting; do not calculate authoritative resets in the browser.
- [ ] Keep component state local and avoid a new global store.

### 5.3 Verify

```powershell
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 exec playwright test tests_web/nvidia-nim-endpoints.spec.js --config=playwright.config.mjs --project=desktop
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run lint:web
git diff --check
```

## Task 6: Add endpoint inventory and local-preflight recovery UI

**Files:**

- Add: `local-control-center/web/src/features/runtime-setup/NvidiaNimEndpointsPanel.tsx`
- Modify carefully: `local-control-center/web/src/features/runtime-setup/RuntimeSetupPanel.tsx`
- Modify: `local-control-center/web/src/features/model-gateway/ProviderAccountsPanel.tsx`
- Modify: `local-control-center/web/src/api/client.ts`
- Modify: `local_control_center/i18n/default_catalog.json`
- Extend: `tests_web/nvidia-nim-endpoints.spec.js`

### 6.1 Write failing interaction tests

- [ ] Prove inventory rows show endpoint id, family, mode, API family, URL host, credential status, health, terms/trial status, and model-sync state without secrets.
- [ ] Prove local preflight is an explicit action and displays all blockers together.
- [ ] Encode the verified RTX 3090 fixture and assert Qwen-Image-Edit displays required 80 GiB versus observed 24 GiB and no start button/action becomes enabled.
- [ ] Prove missing isolated runtime/toolkit/credential/terms actions route to persisted remediation UI rather than issuing local commands.
- [ ] Prove a compatible fixture still requires fresh preflight and approved start; the page never starts on mount/save.
- [ ] Cover loading, empty, blocked, degraded, healthy, and API-error states plus keyboard/focus behavior.

### 6.2 Implement using the Ollama endpoint pattern

- [ ] Reuse endpoint-scoped information architecture and action hierarchy from `OllamaEndpointsPanel` without copying its transport assumptions.
- [ ] Keep local lifecycle actions visibly separate from hosted endpoint validation/model sync.
- [ ] Show source/verification time for official hardware requirements so stale profile data is auditable.
- [ ] Use explicit labels: “Hosted trial (evaluación)”, “Local development”, “Enterprise self-hosted”, and “Partner paid”.
- [ ] Never expose generic “ready” if only account configuration is complete; show configuration, health, production eligibility, and local readiness independently.

### 6.3 Verify

```powershell
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 exec playwright test tests_web/nvidia-nim-endpoints.spec.js tests_web/settings-providers.spec.js --config=playwright.config.mjs --project=desktop
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run lint:web
git diff --check
```

## Task 7: Add multi-model strategy and branch-result UI

**Files:**

- Add: `local-control-center/web/src/features/model-gateway/MultiModelExecutionPanel.tsx`
- Modify: `local-control-center/web/src/features/model-gateway/ModelGatewayPage.tsx`
- Modify: `local-control-center/web/src/api/client.ts`
- Modify: `local_control_center/i18n/default_catalog.json`
- Extend: `tests_web/nvidia-nim-endpoints.spec.js`

### 7.1 Write failing plan/result tests

- [ ] Cover `single`, `parallel_compare`, and `quorum`, including branch selection, `minSuccessful`, reviewer, maximum parallelism, and endpoint-specific quota preview.
- [ ] Prevent duplicate branch ids and incompatible API-family/model combinations before submission, with server errors still authoritative.
- [ ] Prove a persisted execution shows each branch independently: queued, admitted, blocked, running, succeeded, failed, cancelled, latency, usage/cost unknown or reported, and sanitized error reason.
- [ ] Prove partial comparison and failed quorum are not styled or announced as success.
- [ ] Prove refresh/reopen can reconstruct the execution from server state.

### 7.2 Implement the smallest coherent surface

- [ ] Build a plan form from existing enabled endpoint/model data; do not duplicate routing logic in React.
- [ ] Use the server's quota preview and effective maximum parallelism.
- [ ] Submit once with an idempotency key, then poll only the execution read endpoint with bounded backoff while active.
- [ ] Stop polling on terminal/unmounted state and preserve accessible status updates without flooding `aria-live`.

### 7.3 Verify

```powershell
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 exec playwright test tests_web/nvidia-nim-endpoints.spec.js --config=playwright.config.mjs --project=desktop
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 run lint:web
git diff --check
```

## Task 8: Update NVIDIA documentation and run end-to-end proof

**Files:**

- Replace/update: `docs/nvidia-nim-provider.md`
- Update if API behavior changed: `docs/superpowers/specs/2026-07-13-nvidia-nim-endpoints-limits-design.md`
- Test: all focused files above

### 8.1 Document the real operating modes

- [ ] Document creation of multiple hosted-trial, partner-paid, enterprise, and local-development endpoint accounts.
- [ ] Document safe credential-reference configuration without sample real keys.
- [ ] State that NVIDIA-hosted trial allowances are dynamic/unpublished and that configured AIDO limits are operator guardrails, not NVIDIA entitlement discovery.
- [ ] Document policy semantics for daily/monthly/RPM/TPM/concurrency/cost, timezone reset, unknown usage/cost, `Retry-After`, and parallel/quorum branches.
- [ ] Document official support-source URLs, preflight requirements, trial versus production terms, and the current-host blocked result.
- [ ] Document recovery steps without instructing automatic mutation of the shared WSL distro.

### 8.2 Full deterministic verification

```powershell
uv run pytest tests_py/test_nvidia_nim_endpoint_accounts.py tests_py/test_nvidia_nim_capabilities.py tests_py/test_provider_quota_leases.py tests_py/test_provider_metered_pricing.py tests_py/test_multi_model_execution.py tests_py/test_nvidia_nim_local_deployment.py tests_py/test_nvidia_nim_local_broker.py tests_py/test_nvidia_nim_local_api.py -q
uv run pytest tests_py/test_model_runtime_gateway.py tests_py/test_provider_setup_catalog.py tests_py/test_runtime_config.py tests_py/test_runtime_adapters_contract.py tests_py/test_ai_resource_manager.py tests_py/test_remediation_blocker_experience.py tests_py/test_workspace_isolation_contract.py tests_py/test_local_worker_runtime.py tests_py/test_ci_and_openapi_client.py -q
corepack pnpm@10.24.0 run openapi:generate
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 run build:control-center
corepack pnpm@10.24.0 exec playwright test tests_web/nvidia-nim-endpoints.spec.js tests_web/settings-providers.spec.js --config=playwright.config.mjs --project=desktop
corepack pnpm@10.24.0 run lint:py
corepack pnpm@10.24.0 run lint:web
corepack pnpm@10.24.0 run security:secrets
git diff --check
git status --short
```

### 8.3 Credential- and hardware-gated live proof

- [ ] When a NVIDIA API credential reference is configured, sync `/v1/models`, execute one short hosted trial call through AIDO, and verify persisted provider-reported/unknown usage honestly.
- [ ] Execute a two- or three-model parallel plan only when the configured account exposes those models and AIDO guardrails permit the calls.
- [ ] Verify paid/partner calls only with explicit configured pricing, budget, terms, and approval; do not spend merely to prove plumbing.
- [ ] On this RTX 3090 host, run live read-only preflight and prove the documented incompatible/unsupported block occurs before mutation.
- [ ] Run a local NIM start/smoke only on a separate officially supported target with toolkit/CDI, credentials, terms, resources, and explicit approval. Do not substitute a fake container response.

### 8.4 Final self-review checkpoint

- [ ] Search for raw secrets, bearer headers, base64 image data, shell-string construction, hidden side effects, fake prices/zero usage, and unsupported “ready” labels.
- [ ] Confirm page load/health/status paths are free of billable calls and local mutations.
- [ ] Confirm every policy mutation is server-authorized, every lifecycle mutation is approved/audited, and all remote state is re-fetched.
- [ ] Confirm the pre-existing dirty changes remain intact and the final status identifies any live credential/hardware evidence still pending.

No commit or push follows this checkpoint.
