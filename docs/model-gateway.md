# Model Gateway

## What It Does

The Model Gateway is the local-first control surface for model API providers, CLI coding runtimes, routing profiles, role policies, usage, budgets and quota state. It keeps legacy `model_policies`, `model_calls` and `cost_usage` intact while adding the Unified Model & Runtime Gateway tables.

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
- `GET/POST/PATCH /api/v1/model-gateway/routing-profiles`
- `GET/POST/PATCH /api/v1/model-gateway/role-policies`
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute-mock`
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

## Testing

Run:

```powershell
uv run pytest tests_py/test_model_runtime_gateway.py tests_py/test_ci_and_openapi_client.py -q
corepack pnpm@10.24.0 run openapi:generate
```

## Risks

- Real provider execution remains disabled unless explicitly enabled.
- Credential resolution prefers external OpenBao/Vault-compatible refs for real
  provider keys. AppRole bootstrap is the recommended real-use path; `env:` and
  `keyring:` remain development/bootstrap options. AIDO does not persist raw API
  keys. This reduces exposure of provider keys but does not remove secret-zero:
  the local process still needs a vault bootstrap identity.
- Pricing seeds are marked `manual_seed`; treat them as editable estimates, not current truth.

## Limitations

- `route/execute` is present but fail-closed: API provider calls require `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`, configured credentials, budget/quota clearance and no pending approval requirement. If approval is required, it creates a pending `model.route.execute` action request and the overview counts it in `pendingModelApprovals`. CLI execution remains delegated to policy-approved agent runtime sessions.
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
