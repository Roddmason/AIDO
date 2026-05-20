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
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute-mock`

## Testing

Run `uv run pytest tests_py/test_model_runtime_gateway.py -q`.

## Risks

- Real provider execution remains disabled unless explicitly enabled.
- Pricing seeds are marked `manual_seed`; treat them as editable estimates, not current truth.

## Limitations

- The MVP executes only mock routes from HTTP; real provider and CLI execution require explicit safety enablement.

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
