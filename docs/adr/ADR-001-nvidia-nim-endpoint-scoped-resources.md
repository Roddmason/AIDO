# ADR-001: Endpoint-scoped NVIDIA NIM resources

## Estado: Aceptada

## Contexto

AIDO currently represents NVIDIA NIM as one fixed provider id. The requested behavior requires multiple hosted, self-hosted, and paid endpoints; per-endpoint/model daily and concurrency limits; concurrent multi-model execution; typed chat and visual APIs; and honest local hardware compatibility.

Keeping one `nvidia_nim` account would mix credentials, health, quotas, terms, and usage. Routing through a new external gateway would duplicate AIDO's existing provider, policy, usage, and runtime truth boundaries.

## Decisión

Use endpoint-scoped `provider_accounts` as runtime instances and add an explicit `provider_family=nvidia_nim`. Persist `deployment_mode`, `api_family`, and an `adapter_profile` whenever the wire contract is otherwise ambiguous; never infer a visual payload schema from URL text. Evolve the existing Model Gateway and AI resource selection modules. Separate configured limit policy, normalized usage windows, active execution leases, and append-only provider observations. Represent multi-model work as a persisted execution plan with independently auditable branches.

Use capability-specific provider protocols for chat, embeddings, rerank, image generation, and image editing. Keep local NIM lifecycle behind ToolBroker/policy and require hardware, runtime, credential, terms, and port preflight before container mutation.

## Consecuencias positivas

- Multiple NVIDIA endpoints coexist without conflating credentials, health, models, or limits.
- Existing routing, usage, runtime truth, audit, and UI architecture remains authoritative.
- Atomic leases make concurrency and quota enforcement measurable instead of advisory.
- Chat and visual APIs remain type-safe and honest.
- Hosted trial and production-capable deployments can carry different policy and terms.

## Consecuencias negativas

- Exact provider-id checks must become provider-family-aware.
- SQLite schema and OpenAPI contracts grow.
- Synchronous provider transports need a bounded structured-concurrency adapter.
- Live completion still depends on user-supplied NVIDIA/NGC credentials and compatible hardware.

## Alternativas consideradas

### Keep a single `nvidia_nim` provider

Rejected because it cannot isolate multiple accounts/endpoints or their limits and would make runtime truth misleading.

### Put NVIDIA behind LiteLLM only

Rejected as the primary design because it adds an operational dependency, hides NVIDIA-specific health/terms/rate observations, and does not cover NIM visual endpoints cleanly. LiteLLM remains a separately configurable gateway provider.

### Create a standalone NVIDIA subsystem

Rejected because it would duplicate provider accounts, routing, usage ledger, runtime health, remediation, and setup UI.

## Impacto

- **Backend:** additive migrations; provider factory generalization; quota leases/windows; typed adapters; execution orchestration; local deployment preflight.
- **Frontend:** endpoint-scoped NVIDIA setup, limits/usage visibility, strategy configuration, and compatibility remediation.
- **Datos:** provider family/deployment/API/adapter-profile fields plus limit state, observations, pricing components, executions, and branches.
- **Seguridad:** credential refs only; brokered container auth; sanitized provider/image payloads; paid calls remain explicit.
- **Performance:** endpoint bulkheads and bounded worker threads; short SQLite admission transactions.
- **Operación:** local NIM remains opt-in and preflighted; trial hosted endpoints remain non-production.
- **QA:** concurrency races, reset windows, partial quorum, `429`, hardware blocks, generated contracts, UI, and live credential-gated smoke.
