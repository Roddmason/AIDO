# Model Routing

## What It Does

`local_control_center.agents.model_router.ModelRouter` selects a provider, model and runtime from catalog data, role policy, routing mode, privacy, budget, quota, health and capability filters.

## Configuration

The default mode is `balanced_best_value`. Seeds live in SQLite and an editable example is available at `config/model-routing.example.yaml`.

## Endpoints

- `GET /api/v1/model-gateway/routing-profiles`
- `GET /api/v1/model-gateway/role-policies`
- `POST /api/v1/model-gateway/route/preview`
- `GET /api/v1/model-gateway/routing-decisions`

## Testing

Tests cover NVIDIA-first free routing, `local_private` remote blocking, CLI preference for developer code edits, technical lead xhigh escalation, and approval thresholds.

## Risks

- The score is an initial heuristic. It records `scoreBreakdown` so future benchmarks can replace weights with evidence.
- Manual mode does not auto-fallback unless configured.

## Limitations

- Past performance is represented by schema now; benchmark-driven scoring is not active until outcome data exists.

## Example

`local_private` rejects `provider_type=api` and `provider_type=gateway` unless explicitly overridden outside the default router path.
