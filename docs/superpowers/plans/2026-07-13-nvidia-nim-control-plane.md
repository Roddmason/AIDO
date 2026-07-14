# NVIDIA NIM control plane implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task by task. Each task must follow `superpowers:test-driven-development`, preserve unrelated dirty-worktree changes, and end with fresh verification. Do not commit or push.

**Goal:** Convert NVIDIA NIM from one hard-coded chat provider into endpoint-scoped provider accounts with capability-aware adapters, atomic configurable limits, and persisted one-or-many model execution.

**Architecture:** Evolve the existing SQLite-backed Model Gateway rather than creating a second NVIDIA subsystem. `provider_accounts.provider_id` remains the endpoint instance key; `provider_family` identifies NVIDIA. Admission is enforced through transactional quota leases before any network call. Multi-model work is an explicit persisted plan whose branches reuse the same gateway, provider factory, quota, usage, and audit boundaries.

**Tech stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, standard-library HTTP in production, `httpx` only at test boundaries, pytest, existing AIDO repositories and event bus, generated OpenAPI TypeScript client.

**Design source:** `docs/superpowers/specs/2026-07-13-nvidia-nim-endpoints-limits-design.md` and `docs/adr/ADR-001-nvidia-nim-endpoint-scoped-resources.md`.

## Global constraints

- Do not commit, push, stage, reset, reformat unrelated files, or replace user changes in the dirty worktree.
- Use `apply_patch` for edits. Inspect the current diff before every change to an already-modified file.
- Use `corepack pnpm@10.24.0`; never use `npm` or `npx`.
- Add no dependency unless the existing standard library and declared packages demonstrably cannot implement the requirement.
- SQLite is authoritative for provider identity, limits, reservations, executions, usage, and runtime truth.
- Store credential references only. Never persist or return a raw NVIDIA, NGC, partner, or Hugging Face secret.
- Unknown provider usage and unknown cost remain `NULL`; never coerce them to zero.
- NVIDIA hosted trial is evaluation-only and must fail closed for production-tagged work.
- Do not turn image or other specialist APIs into fake chat capability.
- Network fakes are allowed only in tests. Productive code must expose real blocked/unavailable states.
- Every backend response-model change requires OpenAPI and TypeScript client regeneration in the same task.

## Task 1: Add endpoint-scoped provider and model schema

**Files:**

- Modify: `local_control_center/shared/migrations.py`
- Modify: `local_control_center/agents/model_gateway_models.py`
- Modify: `local_control_center/agents/provider_accounts.py`
- Test: `tests_py/test_nvidia_nim_endpoint_accounts.py`
- Regression: `tests_py/test_model_runtime_gateway.py`

### 1.1 Write failing migration and round-trip tests

- [ ] Add a focused test that initializes a fresh database and asserts these provider account columns exist and serialize in camelCase:

```python
assert account["providerFamily"] == "nvidia_nim"
assert account["deploymentMode"] == "hosted_trial"
assert account["apiFamily"] == "chat_completions"
assert account["termsMode"] == "evaluation"
assert account["pricingMode"] == "unknown"
```

- [ ] Assert the legacy seeded `provider_id='nvidia_nim'` row is backfilled without changing its id.
- [ ] Assert two additional account ids, such as `nvidia-hosted-team-a` and `nvidia-partner-prod`, can coexist with separate base URLs, credential refs, health, and metadata.
- [ ] Add model catalog tests for `supportsImageGeneration`, `supportsImageEditing`, and `apiFamily`, preserving the `(provider_id, model)` key.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_nvidia_nim_endpoint_accounts.py -q
```

Expected: failures for missing columns/response fields, not fixture or import errors.

### 1.2 Implement additive phase 52 migration

- [ ] Add `init_phase52_schema(connection)` after phase 51 and before catalog seeding.
- [ ] Add nullable/defaulted provider account columns:

```sql
provider_family TEXT NOT NULL DEFAULT '',
deployment_mode TEXT NOT NULL DEFAULT 'custom',
api_family TEXT NOT NULL DEFAULT 'chat_completions',
terms_mode TEXT NOT NULL DEFAULT 'unspecified',
pricing_mode TEXT NOT NULL DEFAULT 'unknown'
```

- [ ] Backfill existing accounts deterministically from their canonical provider id/api format. In particular, preserve `nvidia_nim` as the endpoint id while setting its NVIDIA family and hosted-trial semantics.
- [ ] Add model fields `api_family`, `supports_image_generation`, and `supports_image_editing` with honest defaults.
- [ ] Make the migration idempotent using existing column-inspection helpers and record version 52 only after all DDL/backfills succeed.
- [ ] Do not migrate authoritative counters out of `current_window_json` yet; that compatibility removal belongs only after parity proof.

### 1.3 Extend Pydantic and store contracts

- [ ] Add strict literals for deployment/API/terms/pricing modes where the existing flexible-request compatibility permits it.
- [ ] Include fields in create, patch, list, and get responses.
- [ ] Validate compact endpoint ids and prevent clients from setting server-owned health fields.
- [ ] Keep legacy payloads valid by supplying server defaults.
- [ ] Ensure store SQL lists columns explicitly; do not rely on `SELECT *` positional order.

### 1.4 Verify the slice

```powershell
uv run pytest tests_py/test_nvidia_nim_endpoint_accounts.py tests_py/test_model_runtime_gateway.py -q
uv run ruff check local_control_center/shared/migrations.py local_control_center/agents/model_gateway_models.py local_control_center/agents/provider_accounts.py tests_py/test_nvidia_nim_endpoint_accounts.py
git diff --check
git status --short
```

Expected: focused tests pass; unrelated dirty files remain untouched.

## Task 2: Generalize catalog setup and provider resolution by family

**Files:**

- Modify: `local_control_center/agents/provider_catalog.py`
- Modify: `local_control_center/agents/provider_catalog_api.py`
- Modify: `local_control_center/agents/model_gateway.py`
- Modify carefully: `local_control_center/agents/model_router.py`
- Add: `local_control_center/agents/providers/factory.py`
- Modify: `local_control_center/agents/runtime_provider_config.py`
- Modify: `local_control_center/runtime_integrations/repository.py`
- Modify: `local_control_center/agents/runtime_status.py`
- Modify: `local_control_center/agents/runtime_adapters.py`
- Modify: `local_control_center/agents/tool_broker.py`
- Modify minimally: `local_control_center/security_policy/policy_engine.py`
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_nvidia_nim_endpoint_accounts.py`
- Regression: `tests_py/test_provider_setup_catalog.py`
- Regression: `tests_py/test_runtime_config.py`

### 2.1 Write failing account-instance and family tests

- [ ] Extend `ProviderAccountFromCatalogRequest` test coverage with separate `catalogProviderId` and endpoint `instanceId` semantics.
- [ ] Prove an `instanceId` is required or deterministically defaults to the catalog id for backward compatibility, and collisions return `409` unless the caller explicitly updates that instance.
- [ ] Prove model sync resolves the catalog entry from stored `metadata.providerCatalogId`, not from the endpoint id.
- [ ] Prove `provider_instance('nvidia-hosted-team-a')` selects the NVIDIA adapter using `providerFamily`, the stored URL, and that account's credential ref.
- [ ] Prove runtime policy uses the family predicate: a NVIDIA endpoint is blocked when NVIDIA policy is disabled even though its id is not exactly `nvidia_nim`.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_nvidia_nim_endpoint_accounts.py tests_py/test_provider_setup_catalog.py -q
```

### 2.2 Separate catalog identity from endpoint identity

- [ ] Change the setup request to accept:

```python
catalog_provider_id: str = Field(alias="providerId")
instance_id: str | None = Field(default=None, alias="instanceId")
```

- [ ] Canonicalize only `catalog_provider_id`. Validate `instance_id` with the existing compact-id rule and persist it as `providerId`.
- [ ] Persist `providerCatalogId`, provider family, deployment mode, API family, terms mode, and pricing mode as explicit columns plus non-authoritative display metadata.
- [ ] Resolve sync presets through `providerCatalogId`, with a safe legacy fallback to provider family/catalog id.

### 2.3 Introduce family-aware provider resolution

- [ ] Make `provider_instance` load the account first, then resolve an adapter from `providerFamily`, `apiFamily`, and account fields.
- [ ] Centralize that dispatch in `ProviderAdapterFactory`; runtime adapters and ToolBroker must delegate to it rather than implement a second NVIDIA transport.
- [ ] Keep legacy provider ids and non-NVIDIA providers behaviorally compatible.
- [ ] Update `NvidiaNimProvider` construction to receive endpoint instance id, base URL, credential ref, and deployment metadata rather than re-reading global `nvidia_nim` configuration.
- [ ] Replace only exact-id checks that express family policy. Do not change role-policy candidate ids, which correctly refer to endpoint instances.
- [ ] Add a small `is_nvidia_nim_account(account)` predicate instead of scattering string-prefix checks.
- [ ] Preserve the broker tool id `nvidia_nim`; carry the selected endpoint instance separately as `providerId`/`runtimeId` so tools are not dynamically registered per account.
- [ ] Permit the endpoint-scoped broker binding only when `runtimeId == providerId` and the stable tool id equals the explicit persisted `providerFamily`; missing or mismatched family metadata remains denied.
- [ ] Regenerate the OpenAPI TypeScript client for the provider-account/catalog response changes introduced by Tasks 1–2 and inspect the generated diff.

### 2.4 Verify the slice

```powershell
uv run pytest tests_py/test_nvidia_nim_endpoint_accounts.py tests_py/test_provider_setup_catalog.py tests_py/test_runtime_config.py -q
corepack pnpm@10.24.0 run openapi:generate
uv run pytest tests_py/test_ci_and_openapi_client.py -q
corepack pnpm@10.24.0 run typecheck:web
uv run ruff check local_control_center/agents/provider_catalog.py local_control_center/agents/provider_catalog_api.py local_control_center/agents/model_gateway.py local_control_center/agents/model_router.py local_control_center/agents/providers/factory.py local_control_center/agents/runtime_provider_config.py local_control_center/runtime_integrations/repository.py local_control_center/agents/runtime_status.py local_control_center/agents/runtime_adapters.py local_control_center/agents/tool_broker.py local_control_center/security_policy/policy_engine.py
git diff --check
```

## Task 3: Add capability-specific provider ports and NVIDIA adapters

**Files:**

- Add: `local_control_center/agents/providers/capabilities.py`
- Modify: `local_control_center/shared/migrations.py`
- Modify: `local_control_center/agents/providers/base.py`
- Modify: `local_control_center/agents/providers/nvidia_nim.py`
- Modify: `local_control_center/agents/model_gateway.py`
- Modify: `local_control_center/agents/model_gateway_api.py`
- Modify: `local_control_center/agents/model_gateway_models.py`
- Modify: `local_control_center/agents/provider_catalog_api.py`
- Modify: `local_control_center/agents/provider_accounts.py`
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_nvidia_nim_capabilities.py`
- Regression: `tests_py/test_model_runtime_gateway.py`
- Regression: `tests_py/test_ci_and_openapi_client.py`

### 3.1 Write failing typed-adapter tests

- [ ] Inject a narrow transport callable/port into the NVIDIA adapter and use `httpx.MockTransport` only from tests to prove chat sends the documented NVIDIA `/chat/completions` request and keeps missing usage unknown. Keep product runtime on its existing standard-library transport; `httpx` is a test extra, not a production dependency.
- [ ] Add typed hosted/self-hosted embedding tests for the documented `/embeddings` contract and typed hosted/self-hosted rerank tests for the documented model-specific `/reranking` versus local `/v1/ranking` contracts.
- [ ] Add typed image-generation and image-editing request/response tests only for endpoint contracts confirmed in current official NVIDIA documentation.
- [ ] Assert selecting `apiFamily=image_editing` through the chat path fails with `unsupported_api_family` before a network call.
- [ ] Assert hosted trial/partner endpoints fail closed without an endpoint credential ref, while documented self-hosted NIM calls may omit `Authorization` entirely. NGC/Hugging Face pull credentials must not be reused as inference bearer tokens.
- [ ] Assert image payload/base64 and bearer headers never appear in returned metadata, logs captured by `caplog`, or audit payloads.
- [ ] Assert discovery without a documented list operation requires an explicit operator manifest and does not scrape HTML.
- [ ] Assert visual execution with `adapterProfile=auto` fails before network, and that a supported explicit adapter profile selects the documented wire contract without inspecting the URL string.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_nvidia_nim_capabilities.py -q
```

### 3.2 Implement narrow provider protocols

- [ ] Define protocols and typed Pydantic records for:

```python
class ChatCompletionProvider(Protocol): ...
class EmbeddingProvider(Protocol): ...
class RerankProvider(Protocol): ...
class ImageGenerationProvider(Protocol): ...
class ImageEditingProvider(Protocol): ...
```

- [ ] Keep existing chat providers working without a repository-wide rewrite. Use runtime-checkable capability resolution at the gateway boundary.
- [ ] Keep the transport port independent of `httpx` types so the production dependency set remains unchanged.
- [ ] Add an additive phase 53 migration for explicit `adapter_profile` provider-account state. Preserve legacy accounts with `auto`; do not reopen or mutate phase 52.
- [ ] Implement only documented NVIDIA contracts. If a configured model/profile has no supported AIDO adapter, persist/display it as unsupported with an explicit reason.
- [ ] Implement all five declared NVIDIA families in this slice: chat completions, embeddings, rerank, image generation, and image editing. A declared protocol without a productive adapter and API route is not considered supported.
- [ ] Route by deployment plus API family: hosted chat/embeddings under `integrate.api.nvidia.com/v1`; hosted visual under the model-specific `ai.api.nvidia.com/v1/genai/...` contract; hosted rerank under `ai.api.nvidia.com/v1/retrieval/.../reranking`; self-hosted retrieval via `/v1/embeddings` or `/v1/ranking`; self-hosted visual via `/v1/infer` or the documented OpenAI image route. Never append a specialist suffix to the chat base URL by assumption.
- [ ] Make endpoint authentication deployment-aware: require credential refs for hosted/partner calls; omit the bearer header for self-hosted endpoints with no configured inference credential. Keep container/model-pull credentials in the local deployment subsystem.
- [ ] Route image outputs through the existing artifact boundary; do not persist binary/base64 in general gateway JSON. If the artifact boundary cannot accept the response safely, fail closed and defer productive image execution rather than returning fake success.
- [ ] Bound payload size, dimensions, timeout, retries, and response decoding.

### 3.3 Expose typed API contracts

- [ ] Add capability-specific request/response models and endpoint(s) under the existing Model Gateway namespace.
- [ ] Return stable `409` reasons for capability/policy/resource conflicts and `422` for malformed payloads.
- [ ] Ensure passive health checks do not issue generation requests.
- [ ] Regenerate the OpenAPI TypeScript client in this task and inspect the generated diff; Task 7 repeats the contract proof but does not defer it.

### 3.4 Verify the slice

```powershell
uv run pytest tests_py/test_nvidia_nim_capabilities.py tests_py/test_model_runtime_gateway.py -q
corepack pnpm@10.24.0 run openapi:generate
uv run pytest tests_py/test_ci_and_openapi_client.py -q
uv run ruff check local_control_center/shared/migrations.py local_control_center/agents/providers local_control_center/agents/model_gateway.py local_control_center/agents/model_gateway_api.py local_control_center/agents/model_gateway_models.py local_control_center/agents/provider_catalog_api.py local_control_center/agents/provider_accounts.py tests_py/test_nvidia_nim_capabilities.py
git diff --check
```

## Task 4: Make quota policy atomic with windows, observations, and leases

**Files:**

- Modify: `local_control_center/shared/migrations.py`
- Replace carefully: `local_control_center/agents/quota_manager.py`
- Modify: `local_control_center/agents/model_router.py`
- Modify: `local_control_center/agents/model_gateway_models.py`
- Modify: `local_control_center/agents/model_gateway_api.py`
- Modify: `local_control_center/agents/providers/nvidia_nim.py`
- Modify: `local_control_center/agents/runtime_adapters.py`
- Add: `tests_py/test_provider_quota_leases.py`
- Regression: `tests_py/test_model_runtime_gateway.py`

### 4.1 Write failing deterministic quota tests

- [ ] Use an injected clock and separate SQLite connections to test minute/day/month windows.
- [ ] Prove `maxConcurrency=2` admits exactly two simultaneous leases and denies a third with `blocked_concurrency`.
- [ ] Prove RPM, TPM, daily/monthly request and token thresholds reset at deterministic boundaries in an IANA timezone.
- [ ] Prove a denied acquisition inserts no lease and changes no reservation.
- [ ] Prove commit converts reserved tokens into committed provider-reported tokens, while unreported usage stays `NULL`/unknown.
- [ ] Prove release/cancellation and lease expiry restore capacity exactly once.
- [ ] Prove `Retry-After` delta-seconds and HTTP-date forms create observations/cooldowns; missing headers use only configured fallback.
- [ ] Prove provider observations never silently raise operator limits.
- [ ] Prove both Model Gateway execution and the ToolBroker `ProviderFactoryAdapter` acquire the same authoritative endpoint/model lease before network dispatch; neither path may bypass quotas.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_provider_quota_leases.py -q
```

### 4.2 Extend limit policy and normalized state schema

- [ ] Add the quota tables/columns in additive phase 54; do not reopen phases 52–53.
- [ ] Add policy columns: `max_concurrency`, `window_timezone`, `enabled`, `fallback_retry_after_seconds`, `max_cost_per_request_usd`, and non-token unit limits needed by supported visual APIs.
- [ ] Create:

```sql
provider_limit_windows(limit_id, window_kind, window_start, committed_requests,
                      committed_tokens, reserved_tokens, known_cost_usd, updated_at)
provider_execution_leases(id, limit_id, provider_id, model, state,
                          reserved_tokens, reserved_units, expires_at,
                          execution_id, branch_id, released_at, created_at, updated_at)
provider_limit_observations(id, provider_id, model, kind, observed_at,
                            retry_after_at, metadata)
```

- [ ] Add uniqueness/indexes needed for one authoritative window row and fast active-lease counting.
- [ ] Store observation metadata only after redaction and allowlist normalization.

### 4.3 Implement acquire/commit/release

- [ ] Replace advisory-only `check()` at execution boundaries with:

```python
lease = quota.acquire(request)
try:
    response = call_provider()
    quota.commit(lease.id, reported_usage=response.usage)
except BaseException:
    quota.release(lease.id, reason="call_failed_or_cancelled")
    raise
```

- [ ] Use `BEGIN IMMEDIATE`/savepoint safely, expire stale leases inside the admission transaction, and make commit/release idempotent.
- [ ] Put acquire/dispatch/commit/release behind one reusable execution boundary consumed by Model Gateway and `ProviderFactoryAdapter`; do not duplicate quota sequencing in the runtime adapter.
- [ ] Mark a lease dispatched immediately before the external call. A request that reached the provider counts as an attempted request even if it later fails; a failure before dispatch does not consume a request window.
- [ ] When usage is unreported, release concurrency but retain the conservative token/unit reservation until its window closes, labelled as unknown settlement. Do not move it into provider-reported committed usage or coerce it to zero.
- [ ] Keep `check()` only as a named preview that clearly does not reserve capacity.
- [ ] Return effective policy, window usage, reservations, cooldown/reset, and denial reason through the existing limit API.
- [ ] Parse provider `429` response headers centrally; remove the hard-coded 300-second NVIDIA behavior.

### 4.4 Verify concurrency and regressions

```powershell
uv run pytest tests_py/test_provider_quota_leases.py tests_py/test_model_runtime_gateway.py -q
uv run ruff check local_control_center/agents/quota_manager.py local_control_center/agents/model_router.py local_control_center/agents/model_gateway_api.py local_control_center/agents/providers/nvidia_nim.py local_control_center/agents/runtime_adapters.py
git diff --check
```

Expected: repeated race test runs admit exactly the configured capacity.

## Task 5: Add metered pricing without inventing cost

**Files:**

- Modify: `local_control_center/shared/migrations.py`
- Modify: `local_control_center/agents/model_gateway_models.py`
- Modify: `local_control_center/agents/provider_accounts.py`
- Modify: `local_control_center/agents/usage_ledger.py`
- Modify: `local_control_center/agents/quota_manager.py`
- Add: `tests_py/test_provider_metered_pricing.py`

### 5.1 Write failing pricing tests

- [ ] Test token, request, image, and second meter types with currency/source/effective timestamps.
- [ ] Test that unknown price or unknown usage yields `costUsd is None`.
- [ ] Test policy outcomes for unknown cost: block, approval required, or allowed under non-cost limits.
- [ ] Test that paid/partner endpoints cannot be treated as free merely because the current call reports no usage.

### 5.2 Implement pricing components

- [ ] Add normalized pricing state in additive phase 55; do not reopen earlier migration versions.
- [ ] Add a normalized `provider_pricing_components` table keyed by provider/model/meter/effective timestamp.
- [ ] Preserve existing token snapshot endpoints and translate them into components without breaking clients.
- [ ] Calculate only from authoritative known quantity and known unit price; otherwise propagate `None`.
- [ ] Feed known cost into quota commit and usage ledger once, with idempotency tied to branch/model call id.

### 5.3 Verify

```powershell
uv run pytest tests_py/test_provider_metered_pricing.py tests_py/test_model_runtime_gateway.py -q
uv run ruff check local_control_center/agents tests_py/test_provider_metered_pricing.py
git diff --check
```

## Task 6: Persist and execute real multi-model plans

**Files:**

- Add: `local_control_center/agents/multi_model_execution.py`
- Add: `local_control_center/agents/multi_model_repository.py`
- Modify: `local_control_center/shared/migrations.py`
- Modify carefully: `local_control_center/agents/ai_resource_manager.py`
- Modify: `local_control_center/agents/model_gateway.py`
- Modify: `local_control_center/agents/model_gateway_api.py`
- Modify: `local_control_center/agents/model_gateway_models.py`
- Modify: `local_control_center/jobs_approvals/worker.py`
- Modify: `local_control_center/jobs_approvals/repository.py`
- Add: `tests_py/test_multi_model_execution.py`
- Regression: `tests_py/test_ai_resource_manager.py`
- Regression: `tests_py/test_model_runtime_gateway.py`

### 6.1 Write failing execution tests

- [ ] Add schema/repository tests for `ai_executions` and `ai_execution_branches` with persisted request hash, strategy, state, provider endpoint, model, role, lease id, result/error evidence, and timestamps.
- [ ] Use controlled blocking test providers to prove three branches overlap in time when `maxParallelism >= 3`.
- [ ] Prove every branch acquires its own quota lease before dispatch.
- [ ] Prove `parallel_compare` returns all successful outputs plus explicit partial failures and overall `partial`.
- [ ] Prove `quorum` succeeds only at `minSuccessful`, runs a configured reviewer only after candidates, and fails with `quorum_not_reached` otherwise.
- [ ] Prove cancellation/timeouts release uncommitted leases and persist terminal branch states.
- [ ] Prove no fallback bypasses endpoint terms, production, privacy, budget, or quota denial.

Run and confirm RED:

```powershell
uv run pytest tests_py/test_multi_model_execution.py -q
```

### 6.2 Implement persisted execution plans

- [ ] Add execution/branch schema in additive phase 56; do not reopen earlier migration versions.
- [ ] Define strict request models:

```python
strategy: Literal["single", "parallel_compare", "quorum"]
branches: list[AIExecutionBranchRequest]
min_successful: int | None
max_parallelism: int
reviewer: AIExecutionBranchRequest | None
```

- [ ] Validate unique branch ids, API-family compatibility, quorum bounds, and maximum configured branch/parallelism ceilings.
- [ ] Persist execution and branch records before network dispatch.
- [ ] Implement structured concurrency with bounded `asyncio.TaskGroup` plus `asyncio.to_thread` for existing synchronous transports. Do not use unbounded thread pools or fire-and-forget tasks.
- [ ] Run multi-model plans through a persisted `model_execution_plan` worker job. The job invokes the same bounded executor; HTTP does not remain open for an arbitrarily long fan-out.
- [ ] Use compare-and-set job ownership/version checks so a worker whose lease expired cannot overwrite a replacement worker's terminal state.
- [ ] Make the existing single route use the same executor path with one branch, retaining compatibility response fields where required.
- [ ] Keep `AIResourceManager` as selection authority. Convert its concrete primary/comparison/reviewer output into an execution plan; do not let the executor rescore candidates.
- [ ] Replace decorative `multiModelQuorum` behavior only after focused parity tests for the existing decision payload.

### 6.3 Add execution APIs

- [ ] Keep `/api/v1/model-gateway/route/execute` backward compatible for the immediate single-model form. Accept an explicit plan by creating the persisted execution/job and returning `202` plus its execution id.
- [ ] Add read-only endpoints for execution and branch inspection; avoid endpoints that expose prompts, secrets, image bytes, or raw provider error bodies.
- [ ] Persist approval jobs before paid or policy-restricted branches; admission remains blocked until approval is actually recorded.
- [ ] Emit structured audit/events for admission, branch start/finish/cancel, and quorum state.

### 6.4 Verify the execution slice

```powershell
uv run pytest tests_py/test_multi_model_execution.py tests_py/test_ai_resource_manager.py tests_py/test_model_runtime_gateway.py tests_py/test_local_worker_runtime.py -q
uv run ruff check local_control_center/agents/multi_model_execution.py local_control_center/agents/multi_model_repository.py local_control_center/agents/ai_resource_manager.py local_control_center/agents/model_gateway.py local_control_center/agents/model_gateway_api.py local_control_center/jobs_approvals
git diff --check
```

## Task 7: Regenerate contracts and run the control-plane proof ladder

**Files:**

- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Modify only if required by generated types: `local-control-center/web/src/api/types.ts`
- Modify only if required by API wrappers: `local-control-center/web/src/api/client.ts`
- Regression: `tests_py/test_ci_and_openapi_client.py`

### 7.1 Regenerate from the live FastAPI schema

```powershell
corepack pnpm@10.24.0 run openapi:generate
```

- [ ] Inspect the generated diff for all new account, limit, capability, execution, and branch fields.
- [ ] Do not hand-edit generated operation types.
- [ ] Add/adjust typed client wrappers only where the existing generator does not expose a convenient function.

### 7.2 Run focused and adjacent backend suites

```powershell
uv run pytest tests_py/test_nvidia_nim_endpoint_accounts.py tests_py/test_nvidia_nim_capabilities.py tests_py/test_provider_quota_leases.py tests_py/test_provider_metered_pricing.py tests_py/test_multi_model_execution.py -q
uv run pytest tests_py/test_model_runtime_gateway.py tests_py/test_provider_setup_catalog.py tests_py/test_runtime_config.py tests_py/test_ai_resource_manager.py tests_py/test_ci_and_openapi_client.py -q
uv run ruff check local_control_center tests_py/test_nvidia_nim_endpoint_accounts.py tests_py/test_nvidia_nim_capabilities.py tests_py/test_provider_quota_leases.py tests_py/test_provider_metered_pricing.py tests_py/test_multi_model_execution.py
corepack pnpm@10.24.0 run typecheck:web
git diff --check
git status --short
```

### 7.3 Self-review checkpoint

- [ ] Trace each changed production line to an acceptance criterion.
- [ ] Search for stale exact-id NVIDIA checks and classify each as endpoint id or family policy.
- [ ] Search for fake zeros/default prices, raw secrets, image base64, unbounded concurrency, and passive billable calls.
- [ ] Inspect overlap with pre-existing dirty files and restore no user-owned changes.
- [ ] Record any credential-gated live proof as pending, never as passed through a mock.

No commit or push follows this checkpoint.
