# Model Gateway

## What It Does

The Model Gateway is the local-first control surface for model API providers, CLI coding runtimes, routing profiles, role policies, usage, budgets and quota state. It keeps legacy `model_policies`, `model_calls` and `cost_usage` intact while adding the Unified Model & Runtime Gateway tables.
Runtime execution is fail-closed: planning can record `planned`, but only a real
provider/runtime response can record `completed`.

## Configuration

- Keep real calls disabled by default: `AIDO_ENABLE_REAL_PROVIDER_CALLS=false`.
- Enable providers through `/api/v1/model-gateway/providers/{id}`.
- Store only `credential_ref` values such as
  `openbao:secret/providers/nvidia_nim#api_key`; never store raw keys. See
  `docs/credentials.md`.

## Endpoints

- `GET /api/v1/model-gateway/overview`
- `GET/POST/PATCH /api/v1/model-gateway/providers`
- `GET/POST/PATCH /api/v1/model-gateway/models`
- `GET/POST /api/v1/model-gateway/pricing-snapshots`
- `GET/POST/PATCH /api/v1/model-gateway/routing-profiles`
- `GET/POST/PATCH /api/v1/model-gateway/role-policies`
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute`
- `GET /api/v1/model-gateway/usage-ledger`
- `GET /api/v1/model-gateway/usage-ledger/summary`
- `GET /api/v1/model-gateway/routing-decisions`
- `GET /api/v1/model-gateway/benchmarks`
- `GET/POST /api/v1/model-gateway/benchmark-outcomes`
- `GET/PATCH /api/v1/model-gateway/provider-limits`
- `GET/POST/PATCH /api/v1/model-gateway/budget-rules`
- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/detect`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/health-check`
- `GET /api/v1/model-gateway/cli-sessions`
- `GET /api/v1/model-gateway/cli-sessions/{id}`

## Testing

Run:

```powershell
uv run pytest tests_py/test_model_runtime_gateway.py tests_py/test_ci_and_openapi_client.py -q
uv run pytest tests_py/test_internal_mock_product_boundary.py tests_py/test_real_readiness_architecture.py -q
corepack pnpm@10.24.0 run openapi:generate
```

## Risks

- Real provider execution remains disabled unless explicitly enabled.
- Routing candidates are fail-closed. A seeded catalog row is not enough to
  route work: non-manual providers must be enabled, healthy, and have a real
  `lastHealthCheckAt` from provider health checking before the router can select
  them. Manual routing is allowed only in `manual_by_profile` mode.
- Credential resolution prefers external OpenBao/Vault-compatible refs for real
  provider keys. AppRole bootstrap is the recommended real-use path; `env:` and
  `keyring:` remain development/bootstrap options. AIDO does not persist raw API
  keys. This reduces exposure of provider keys but does not remove secret-zero:
  the local process still needs a vault bootstrap identity.
- Pricing seeds are marked with `source` and staleness; treat manual seeds as
  editable estimates, not current truth or availability. Unknown prices are not
  free unless `freeTier=true`.
- Pricing snapshots are append-only records for manual catalog updates. A
  snapshot stores redacted `sourceRef`/metadata, can optionally apply prices to
  `model_catalog`, and marks the model source as `pricing_snapshot:{id}`.

## Limitations

- `route/execute` is present but fail-closed: API provider calls require `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`, configured credentials, budget/quota clearance and no pending approval requirement. If approval is required, it creates a pending `model.route.execute` action request and the overview counts it in `pendingModelApprovals`. CLI execution remains delegated to policy-approved agent runtime sessions.
- `ModelGateway.execute_model_call()` only supports real provider execution.
  Missing configuration returns `configuration_required`; disabled execution
  gates return `blocked`; configured providers whose endpoint fails return
  `unavailable` with a redacted technical reason.
- Usage records only contain provider-reported token counts. If a provider
  response lacks usage, token state is `unknown`/`unavailable`; if configured
  pricing is missing, `costStatus` is `unknown` and cost fields remain null.
- Provider health checks are fail-closed. Disabled providers return
  `configuration_required`; API/gateway providers require a configured
  credential and `AIDO_ENABLE_REAL_PROVIDER_CALLS=true` before any remote
  health adapter is called. Real 429 responses update provider cooldown state.
- Provider model discovery is real-only. API/gateway discovery requires the
  provider to be enabled, `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`, and a valid
  credential reference before the adapter is called. Discovery audit payloads
  record provider source and model count, never mock source.
- Benchmarks derive usage/cost/latency from `usage_ledger` and success/QA/rework from `model_benchmark_outcomes`. Outcomes can be recorded manually from the console or automatically during evidence creation when the evidence includes `usageLedgerId` or model identity fields.

## Example

```json
{
  "role": "developer",
  "taskType": "implementation",
  "mode": "balanced_best_value",
  "requiresCodeEdit": true,
  "budgetRemainingUsd": 4.2
}
```
