# Model Gateway

## What It Does

The Model Gateway is the local-first control surface for model API providers, CLI coding runtimes, routing profiles, role policies, usage, budgets and quota state. It keeps legacy `model_policies`, `model_calls` and `cost_usage` intact while adding the Unified Model & Runtime Gateway tables.

## Configuration

- Keep real calls disabled by default: `AIDO_ENABLE_REAL_PROVIDER_CALLS=false`.
- Enable providers through `/api/v1/model-gateway/providers/{id}`.
- Store only `credential_ref` values such as `NVIDIA_NIM_API_KEY`; never store raw keys.

## Endpoints

- `GET /api/v1/model-gateway/overview`
- `GET/POST/PATCH /api/v1/model-gateway/providers`
- `GET/POST/PATCH /api/v1/model-gateway/models`
- `GET/POST/PATCH /api/v1/model-gateway/routing-profiles`
- `GET/POST/PATCH /api/v1/model-gateway/role-policies`
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute-mock`
- `GET /api/v1/model-gateway/usage-ledger`
- `GET /api/v1/model-gateway/usage-ledger/summary`
- `GET /api/v1/model-gateway/routing-decisions`
- `GET /api/v1/model-gateway/benchmarks`
- `GET/PATCH /api/v1/model-gateway/provider-limits`
- `GET/POST/PATCH /api/v1/model-gateway/budget-rules`
- `GET /api/v1/model-gateway/cli-runtimes`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/detect`
- `POST /api/v1/model-gateway/cli-runtimes/{id}/health-check`
- `GET /api/v1/model-gateway/cli-sessions`

## Testing

Run:

```powershell
uv run pytest tests_py/test_model_runtime_gateway.py tests_py/test_ci_and_openapi_client.py -q
corepack pnpm@10.24.0 run openapi:generate
```

## Risks

- Real provider execution remains disabled unless explicitly enabled.
- Pricing seeds are marked `manual_seed`; treat them as editable estimates, not current truth.

## Limitations

- The HTTP execution route remains `execute-mock`; real provider and CLI execution require explicit safety enablement and should be added behind policy gates, not as a silent fallback.
- Benchmarks derive usage/cost/latency from `usage_ledger`; success, QA pass rate and rework stay empty until outcome records are collected.

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
