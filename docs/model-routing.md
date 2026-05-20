# Model Routing

## What It Does

`local_control_center.agents.model_router.ModelRouter` selects a provider, model and runtime from catalog data, role policy, routing mode, privacy, budget, quota, health and capability filters.

Workflow start now calls the router once per materialized workflow step. The decision is recorded with `workflowRunId`, `workflowStepId` and `taskId`, but no real provider or CLI execution is triggered by starting a workflow.

## Configuration

The default mode is `balanced_best_value`. Seeds live in SQLite and an editable example is available at `config/model-routing.example.yaml`.

## Endpoints

- `GET /api/v1/model-gateway/routing-profiles`
- `GET /api/v1/model-gateway/role-policies`
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute`
- `GET /api/v1/model-gateway/routing-decisions`
- `GET /api/v1/model-gateway/benchmarks`

## Testing

Tests cover NVIDIA-first free routing, `local_private` remote blocking, CLI preference for developer code edits, technical lead xhigh escalation, approval thresholds, fail-closed real execution and workflow-step routing decision linkage.

## Risks

- The score is an initial heuristic. It records `scoreBreakdown` so future benchmarks can replace weights with evidence.
- Manual mode does not auto-fallback unless configured.

## Limitations

- Benchmark-derived scoring is not active yet. The benchmark endpoint exposes usage-derived attempts/cost/latency and outcome-derived success/QA/rework rates once evidence or operators record outcomes.
- Real execution does not override approval gates. If a selected route requires approval, `/route/execute` creates a pending `model.route.execute` action request and returns `409` before any provider or CLI call.

## Example

`local_private` rejects `provider_type=api` and `provider_type=gateway` unless explicitly overridden outside the default router path.
