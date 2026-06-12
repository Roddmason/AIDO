# Model Routing

## What It Does

`local_control_center.agents.model_router.ModelRouter` selects a provider, model and runtime from catalog data, role policy, routing mode, privacy, budget, quota, health and capability filters. Route preview exposes `policyResult`, `budgetResult` and `quotaResult` so callers can see why a route was allowed, blocked or approval-gated.

Workflow start now calls the router once per materialized workflow step. The decision is recorded with `workflowRunId`, `workflowStepId` and `taskId`, but no real provider or CLI execution is triggered by starting a workflow.

## Configuration

The default mode is `balanced_best_value`. Seeds live in SQLite and an editable example is available at `config/model-routing.example.yaml`.

Pricing catalog records expose `source` and staleness (`fresh`, `stale` or
`unknown`). Unknown prices are not treated as zero unless the model is explicitly
marked `freeTier=true`.

Role policies include `allowUnknownCost` and
`requireApprovalForUnknownCost`. Defaults are conservative for remote
`api`/`gateway` providers: a selected route with `estimatedCostUsd=null`
requires approval, and setting `allowUnknownCost=false` rejects that candidate
with `unknown_remote_cost_not_allowed`. This prevents providers without real
pricing from winning as if they were low-cost.

Manual pricing changes should be recorded through
`POST /api/v1/model-gateway/pricing-snapshots`. When a snapshot is applied to
the catalog, the model source becomes `pricing_snapshot:{id}` so routing
decisions can be traced back to the price input used at the time.

## Endpoints

- `GET /api/v1/model-gateway/routing-profiles`
- `GET /api/v1/model-gateway/role-policies`
- `POST /api/v1/model-gateway/route/preview`
- `POST /api/v1/model-gateway/route/execute`
- `GET /api/v1/model-gateway/routing-decisions`
- `GET /api/v1/model-gateway/benchmarks`

## Testing

Tests cover NVIDIA-first free routing, `local_private` remote blocking, CLI preference for developer code edits, technical lead xhigh escalation, approval thresholds, budget deny/approval behavior, quota cooldown diagnostics, pricing staleness, benchmark confidence thresholds, fail-closed real execution and workflow-step routing decision linkage.

## Risks

- The score is an initial heuristic. It records `scoreBreakdown`; benchmarks can
  influence the score only after the minimum sample threshold is met.
- Manual mode does not auto-fallback unless configured.
- Budget actions are explicit: `deny`, `require_approval`, `fallback` and `warn`.

## Limitations

- Benchmark-derived scoring is deliberately bounded. Insufficient benchmark data
  is surfaced as `benchmarkInsufficientData=true` and cannot dominate routing.
  With sufficient samples, benchmark performance contributes a small additive
  score factor after hard filters for policy, budget, quota, context and privacy.
- Benchmark scoring uses only objective provenance: `automated_run` and
  `release_validation`. `operator_reported` outcomes are listed and counted as
  manual reports, but they do not satisfy the sample threshold and do not add
  `benchmarkContribution`.
- Real execution does not override approval gates. If a selected route requires approval, `/route/execute` creates a pending `model.route.execute` action request and returns `409` before any provider or CLI call.

## Example

`local_private` rejects `provider_type=api` and `provider_type=gateway` unless explicitly overridden outside the default router path.
