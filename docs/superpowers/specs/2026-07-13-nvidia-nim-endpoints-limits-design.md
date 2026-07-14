# NVIDIA NIM endpoint, limit, and parallel execution design

Date: 2026-07-13
Status: Accepted for implementation planning
Scope: AIDO Model Gateway, runtime truth, provider setup, quota enforcement, multi-model execution, and local NVIDIA NIM deployment preflight

## Objective

Make NVIDIA NIM a real, fail-closed provider family in AIDO that can:

- use every NVIDIA-hosted chat model exposed to the configured developer account;
- configure NVIDIA-hosted trial, self-hosted development, self-hosted NVIDIA AI Enterprise, and paid partner endpoints independently;
- execute one model or several models concurrently for comparison or quorum;
- enforce configurable request, token, cost, daily, monthly, and concurrency limits before network calls;
- record provider-reported usage and observed rate-limit state without inventing usage, prices, or quota;
- support NVIDIA chat and visual generation/editing through capability-specific contracts;
- preflight and launch only local NIMs compatible with the actual host and accepted license terms.

“Every free model” means every model that the configured NVIDIA account exposes through a supported AIDO API family. AIDO must not scrape undocumented Build UI endpoints or claim support for healthcare, biology, simulation, speech, or other domain APIs that do not implement one of the supported contracts. Unsupported catalog entries remain visible as unsupported, with an explicit reason.

## Verified current state

AIDO already has useful foundations:

- `NvidiaNimProvider` uses the OpenAI-compatible hosted endpoint and records a fixed cooldown after `429`.
- `provider_accounts`, `model_catalog`, `provider_limits`, `usage_ledger`, routing policies, runtime health, and guided provider setup already exist.
- `provider_limits` contains RPM, TPM, daily, monthly, and budget fields, but RPM is not enforced, counters are not advanced/reset as a transactional window, and concurrency is not modeled.
- provider identity is currently fixed to `nvidia_nim`, so separate hosted, local, and paid accounts cannot coexist honestly.
- `AIResourceManager` can recommend a reviewer and emits `multiModelQuorum`, but there is no execution plan that actually fans calls out in parallel.
- the provider base contract is chat-only. Image generation and image editing cannot be represented honestly through it.

Verified host state on 2026-07-13:

- NVIDIA GeForce RTX 3090, 24 GiB VRAM, Ampere compute capability 8.6, driver 595.79;
- WSL2 Ubuntu 24.04 is running with 24 GiB RAM and 8 GiB swap;
- Podman 4.9.3 is installed, but NVIDIA Container Toolkit is absent;
- Docker Desktop client is installed, but its daemon and WSL integration are not running;
- no `NGC_API_KEY`, `NVIDIA_API_KEY`, `NVIDIA_NIM_API_KEY`, or `HF_TOKEN` is present in the inspected host/WSL environment;
- NVIDIA's current NIM-on-WSL2 prerequisites support GeForce RTX 40-series and 50-series GPUs, so the RTX 3090 is outside the officially supported local WSL2 path;
- Qwen-Image and Qwen-Image-Edit require 80 GiB VRAM according to NVIDIA's current support matrix and therefore cannot run locally on this GPU;
- MiniMax M3 exposes a hosted free endpoint but no downloadable NIM;
- DeepSeek V4 Pro exposes hosted and downloadable options, but its documented supported GPUs do not include the RTX 3090.

These facts are preflight evidence, not permanent assumptions. AIDO will re-check the environment before any deployment.

## Architectural decision

Represent a provider account as an endpoint-scoped runtime instance and keep NVIDIA as a provider family.

Examples:

- `nvidia-hosted-trial` with `providerFamily=nvidia_nim` and `deploymentMode=hosted_trial`;
- `nvidia-local-trellis` with `providerFamily=nvidia_nim` and `deploymentMode=self_hosted_development`;
- `nvidia-partner-prod` with `providerFamily=nvidia_nim` and `deploymentMode=partner_paid`.

This follows AIDO's existing endpoint-scoped Ollama pattern. It avoids duplicate architecture while removing exact-id checks that currently assume NVIDIA has only one endpoint.

## Bounded components

### 1. Provider instance and model catalog

Extend `provider_accounts` additively with explicit fields rather than hiding core routing data in metadata:

- `provider_family`;
- `deployment_mode`;
- `api_family`;
- `adapter_profile`;
- `terms_mode`;
- `pricing_mode`.

`provider_id` remains the unique endpoint instance id. Existing `nvidia_nim` rows migrate to `providerFamily=nvidia_nim`, `deploymentMode=hosted_trial`, and `apiFamily=chat_completions` without changing their id.

`adapter_profile` selects a documented wire contract when `deployment_mode + api_family` is not sufficient. Backward-compatible chat accounts default to `auto`, which may resolve only unambiguous chat/embedding/rerank combinations. Visual `auto` is fail-closed: operators select a supported profile such as NVIDIA native infer or an explicitly documented OpenAI-compatible image route. AIDO never guesses a payload contract from a URL substring.

Supported API families for this slice are:

- `chat_completions`;
- `embeddings`;
- `rerank`;
- `image_generation`;
- `image_editing`.

The catalog remains keyed by `(provider_id, model)`, so availability and enablement are endpoint-specific. Add explicit capability flags for image generation and image editing. A model may be discovered but disabled or unsupported.

Credential semantics are deployment-specific. NVIDIA hosted trial and paid/partner HTTP endpoints require an endpoint credential reference. NVIDIA's documented self-hosted LLM and Visual NIM examples invoke the local inference API without bearer authentication, so `self_hosted_development` and `self_hosted_enterprise` allow an empty endpoint credential reference unless an operator has placed an authenticated proxy in front of NIM. NGC/Hugging Face credential references used to pull containers or model assets belong to the local deployment definition and must never be reused automatically as inference bearer tokens.

Hosted chat discovery uses the documented `/v1/models` contract. Self-hosted NIM discovery uses `/v1/models`, `/v1/metadata`, or `/v1/manifest` only where NVIDIA documents that endpoint for the selected NIM. API families without a model-list operation use an operator-confirmed model manifest; AIDO does not scrape HTML.

The currently verified NVIDIA transport map is not uniform:

- hosted chat and embeddings use `https://integrate.api.nvidia.com/v1` with `/chat/completions`, `/models`, and `/embeddings` as applicable;
- hosted visual generation/editing uses model-specific `https://ai.api.nvidia.com/v1/genai/{publisher}/{model}` contracts unless the model page documents another route;
- hosted reranking uses `https://ai.api.nvidia.com/v1/retrieval/{publisher}/{model}/reranking`;
- self-hosted retrieval uses `/v1/embeddings` or `/v1/ranking` as applicable;
- self-hosted Visual GenAI uses `/v1/infer`, with `/v1/images/generations` or `/v1/images/edits` only for models explicitly listed by NVIDIA as OpenAI-compatible.

Therefore `api_family` plus `adapter_profile` select a typed contract and a deployment-specific route template. `base_url` is the explicitly configured, versioned root for that contract: chat/embeddings append their resource to a `/v1` root; hosted rerank appends `/reranking` to its model-specific retrieval root; self-hosted rerank appends `/ranking` to its `/v1` root. AIDO must not add a second `/v1`, derive a specialist endpoint from the chat base URL, or inspect URL text to guess the request schema.

### 2. Capability-specific provider ports

Replace the oversized assumption that every model is chat with small provider protocols:

- `ChatCompletionProvider`;
- `EmbeddingProvider`;
- `RerankProvider`;
- `ImageGenerationProvider`;
- `ImageEditingProvider`.

`ProviderAdapterFactory` resolves an adapter from `provider_family`, `api_family`, and the endpoint account. Existing chat providers continue to implement the chat protocol. NVIDIA visual adapters use the documented OpenAI-compatible image endpoints when available and a model-specific hosted or self-hosted `/v1/infer` adapter otherwise. Qwen Image Edit self-hosted supports `/v1/infer` and `/v1/images/edits`; its request and response are not chat completions.

Requests and responses are typed per capability. An image response stores or references an artifact through AIDO's artifact boundary; base64 payloads are not written to logs, routing decisions, or general JSON metadata.

### 3. Limit policy, windows, and leases

Keep `provider_limits` as the operator-configured policy table and add:

- `max_concurrency`;
- `window_timezone` with default `UTC`;
- `enabled`;
- optional per-request and per-unit budget fields where token pricing is not applicable.

Do not keep authoritative counters solely in `current_window_json`. Add normalized state:

- `provider_limit_windows`: one row per limit/window start, with committed requests, committed tokens, reserved tokens, and known cost;
- `provider_execution_leases`: one row per admitted branch, with state, reservation, expiry, workflow/task correlation, and release timestamp;
- `provider_limit_observations`: append-only provider evidence such as `429`, parsed `Retry-After`, rate-limit headers, and sanitized error class.

`QuotaManager.acquire()` performs one immediate SQLite transaction that:

1. resolves the model-specific limit, falling back to the endpoint wildcard;
2. expires stale leases;
3. calculates the relevant minute/day/month windows in the configured IANA timezone;
4. checks RPM, TPM, daily/monthly requests and tokens, cost, cooldown, and active lease count;
5. inserts a lease and increments reservations only when every gate passes.

`QuotaManager.commit()` converts reservations to actual provider-reported usage. Unknown usage remains unknown and does not become zero. `QuotaManager.release()` removes reservations after failures or cancellation. Lease expiry prevents capacity from being stranded after process failure.

NVIDIA `429` handling parses `Retry-After` when present and records an observation. A configurable conservative fallback is used only when the provider gives no reset signal. Provider-observed limits never overwrite operator guardrails silently.

### 4. Pricing and paid endpoints

Keep existing token prices for chat models and add metered pricing components for non-token APIs:

- meter type such as `input_tokens`, `output_tokens`, `request`, `image`, or `second`;
- unit price and currency;
- effective timestamp and source reference.

Paid endpoints may use configured authoritative pricing snapshots. When a provider does not return usage or pricing is not known, cost stays `NULL`; policy decides whether unknown-cost execution is blocked, requires approval, or is allowed within non-cost limits.

Hosted NVIDIA trial endpoints are labeled evaluation-only. AIDO must not mark them production-ready. Production policy accepts only `self_hosted_enterprise` or an explicitly configured paid partner endpoint with suitable terms.

### 5. Multi-model execution

Introduce an explicit `AIExecutionPlan` rather than treating `multiModelQuorum` as a decorative boolean.

Strategies:

- `single`: one branch;
- `parallel_compare`: N independent branches, preserve every output, no automatic winner unless a reviewer is configured;
- `quorum`: N branches with `minSuccessful`, followed by an optional reviewer/aggregator branch.

Every branch contains provider instance, model, API family, role, maximum tokens/units, timeout, and limit reservation. A plan also contains `maxParallelism`; it cannot exceed the smallest applicable project, provider, or endpoint bulkhead.

`MultiModelExecutor` persists the execution and branch records before dispatch, acquires one quota lease per branch, and runs admitted branches with structured concurrency. Existing synchronous HTTP adapters run through bounded worker threads; cancellation releases uncommitted leases. Results are persisted independently so a partial failure remains auditable.

Failure policies are explicit:

- `single` fails if its branch fails;
- `parallel_compare` returns partial results with an overall `partial` state;
- `quorum` succeeds only when `minSuccessful` branches complete and any required reviewer completes;
- no fallback may bypass provider, cost, privacy, terms, or quota policy.

The existing `AIResourceManager` remains the selection authority. It emits concrete primary, comparison, and reviewer selections. The executor does not rescore or invent candidates.

### 6. API and UI contract

Evolve the existing Model Gateway endpoints instead of creating a parallel NVIDIA control plane.

Required API behavior:

- create and update multiple endpoint-scoped NVIDIA provider accounts;
- discover/sync models for one endpoint;
- upsert and inspect limits for endpoint/model;
- preview effective limits and current windows;
- create an execution from a single or multi-model plan;
- inspect execution branches and usage evidence;
- explicitly test a provider/model without implying production readiness;
- preflight, start, stop, and inspect supported local NIM deployments through brokered actions.

The setup UI adds:

- deployment mode selection;
- endpoint/base URL and safe credential reference;
- model sync and capability status;
- terms/trial warning;
- daily/monthly/RPM/TPM/concurrency/budget editor;
- current usage, reservations, cooldown, and reset time;
- role assignment and multi-model strategy configuration;
- local hardware compatibility and remediation actions.

All backend response-model changes require OpenAPI and TypeScript client regeneration.

### 7. Local NIM deployment

Local deployment is a separate operational adapter behind ToolBroker/policy, not a side effect of provider CRUD.

Preflight checks:

- GPU architecture and VRAM;
- supported model profile and precision;
- driver version;
- host/WSL RAM and swap;
- free disk and cache path;
- container runtime and NVIDIA Container Toolkit;
- port conflicts;
- required NGC/Hugging Face credential refs;
- accepted model and NVIDIA terms.

An incompatible model returns `blocked_incompatible_hardware` with concrete requirements. A missing credential or toolkit returns a persisted remediation; AIDO does not print or persist the secret value.

The current host must not start any local NVIDIA NIM through the supported WSL2 path because its RTX 3090 is outside NVIDIA's supported GeForce generations. Qwen-Image, Qwen-Image-Edit, MiniMax M3, and DeepSeek V4 Pro also have model-specific blockers described above. A compatible candidate may be selected only on a separate supported target after a fresh official support-matrix check and local preflight. Because the current WSL distro also hosts another k3s workload, AIDO must not silently alter global WSL memory or install GPU tooling into it. The deployment plan must use an explicitly selected isolated runtime or require an explicit brokered remediation for shared-host changes.

## Error semantics

Execution and setup expose stable machine-readable reasons:

- `credential_missing`;
- `terms_not_accepted`;
- `unsupported_api_family`;
- `model_not_available`;
- `blocked_budget`;
- `blocked_quota`;
- `blocked_concurrency`;
- `provider_rate_limited`;
- `provider_unavailable`;
- `blocked_incompatible_hardware`;
- `local_runtime_not_ready`;
- `quorum_not_reached`.

Expected policy/resource conflicts use `409`; invalid requests use `422`; missing records use `404`; unexpected internal failures remain `500` with sanitized public detail and structured internal telemetry.

## Security and compliance

- Persist credential references only; never persist raw NVIDIA, NGC, partner, or Hugging Face tokens.
- Keep hosted API credentials (`nvapi-*`) distinct from NGC registry credentials used to authenticate to `nvcr.io`; a field or environment-variable name does not prove the credential kind.
- Pass registry credentials via stdin or an ephemeral brokered secret channel; never command-line arguments.
- Redact provider bodies, headers, URLs with embedded credentials, image base64, and external error payloads.
- Do not execute paid or unknown-cost test prompts from passive health/status reads.
- Mark NVIDIA-hosted trial usage as evaluation/prototyping only.
- Require explicit policy and terms state before production-tagged workloads use an endpoint.
- Bound payload size, image dimensions, timeouts, retries, and concurrent work.

## Observability

Emit structured events and metrics for:

- admission allowed/denied by reason;
- active/reserved concurrency per endpoint;
- requests/tokens/cost per configured window;
- provider `429` and observed reset time;
- branch latency, result state, and cancellation;
- quorum success/failure;
- local NIM health, queue depth, GPU utilization, and profile metadata when documented metrics are available.

Never use a health read to trigger a billable generation call.

## Migration and rollback

Migration is additive:

1. add provider-family/deployment fields and backfill existing rows;
2. extend provider-limit policy and create window/lease/observation tables;
3. add execution/branch persistence and pricing components;
4. keep existing single-provider APIs compatible while the frontend moves to endpoint ids;
5. generalize exact `nvidia_nim` checks to provider-family predicates;
6. remove deprecated JSON counter authority only after parity tests and data migration.

Rollback disables new endpoint instances and multi-model execution while preserving existing `nvidia_nim` chat behavior. Additive tables can remain unused; no destructive down-migration is required.

## Acceptance criteria

1. Two NVIDIA hosted accounts and one self-hosted NIM can coexist with different credentials, URLs, health, models, and limits.
2. Hosted trial models returned by `/v1/models` can be synced without hardcoding a stale catalog.
3. Chat, embeddings, rerank, image generation, and image editing use capability-specific typed adapters and the documented deployment-specific endpoints.
4. A configured `maxConcurrency=2` admits only two active branches atomically, including simultaneous requests from different executions.
5. RPM, TPM, daily/monthly requests, tokens, and known cost are enforced with deterministic window resets.
6. Unknown tokens/cost remain `NULL`/unknown and do not become zero.
7. A `429` with `Retry-After` blocks subsequent routing until the observed reset; a missing header uses only the configured conservative fallback.
8. A three-model quorum runs branches concurrently, respects per-endpoint leases, persists partial results, and fails closed when `minSuccessful` is not met.
9. Paid/partner endpoints obey configured pricing and budget; passive health reads never incur generation cost.
10. NVIDIA-hosted trial endpoints cannot be selected for production-tagged work.
11. Local preflight blocks Qwen-Image-Edit on the verified RTX 3090 with the documented 80 GiB requirement and does not start a container.
12. On an officially supported target, a compatible local NIM can be started only after toolkit, resources, credentials, terms, and port checks pass; health and model sync then update runtime truth. The current RTX 3090 host remains blocked without mutation.
13. Secrets, image data, and bearer headers are absent from API responses, logs, audit metadata, and persisted routing decisions.
    A self-hosted endpoint can execute without a fabricated bearer token, while hosted/partner endpoints still fail closed without their endpoint credential reference.
14. The runtime setup UI configures multiple NVIDIA endpoints and exposes effective limits, current usage, reset/cooldown, and local incompatibility remediation.
15. Focused Python tests, migration tests, OpenAPI generation checks, web typecheck/build, targeted Playwright flows, Ruff, Biome, secret scan, and a credential-gated live hosted/local smoke all pass after the final change.

## Verification strategy

Use deterministic fakes only at network boundaries in tests; no productive mock success paths.

The proof ladder is:

1. RED tests for schema, endpoint identity, atomic leases, window resets, observed `429`, typed image calls, and multi-model execution;
2. focused backend suites for provider accounts, quota, routing, gateway execution, runtime status, and local deployment preflight;
3. migration and OpenAPI/client generation checks;
4. frontend typecheck, build, focused component/Playwright flows, and accessibility assertions;
5. Ruff, Biome, architecture/productive-truth checks, diff check, and secret scan;
6. live hosted smoke only when an NVIDIA API credential ref is configured;
7. local NIM smoke only on an officially supported target for a support-matrix-compatible model after explicit preflight and credential setup.

The goal is not complete if credential-gated live behavior cannot be proven; it remains explicitly pending rather than being replaced by a mock claim.

## Risks and mitigations

- **SQLite concurrency:** use short immediate transactions, unique window keys, lease expiry, and concurrency tests with separate connections.
- **Provider catalog drift:** discover from documented provider endpoints and preserve source/effective timestamps; do not scrape Build internals.
- **Trial limits are unpublished/dynamic:** distinguish configured guardrails from observed provider evidence.
- **Partial multi-model failure:** persist branch results and apply explicit `minSuccessful` semantics.
- **Resource exhaustion:** enforce per-endpoint bulkheads, payload limits, timeouts, and local hardware preflight.
- **Dirty worktree overlap:** preserve existing `AIResourceManager`, `model_router`, migration, coordinator, and UI changes; patch only lines required by this slice and validate focused diffs before broad checks.
