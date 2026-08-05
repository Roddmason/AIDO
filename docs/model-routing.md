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

## Role pins: which roles have one and why the rest do not

A role policy has three preference lists (`preferred`, `fallback`, `escalation`), each a list of
`{provider, model}` pairs. `AIResourceManager` ranks candidates by tier: an exact `{provider, model}`
match wins (tier 0), a provider-level wildcard (`""`, `"*"`, `"auto"`, `"auto_best_available"` — the
shared set in `agents/model_wildcards.py`) comes next (tier 1), and everything else ties at tier 2,
where the score decides.

The migrations seed model pins for a subset of roles (`product_owner`, `technical_lead`,
`backend_engineer`, `frontend_engineer`, `release_manager`, plus legacy ids such as `developer` and
`qa`). The remaining roles of `ALL_ROLES` — `aido_lead`, `project_manager`, `scrum_master`,
`architect`, `mobile_engineer`, `database_engineer`, `data_engineer`, `qa_engineer`,
`security_engineer`, `pentester`, `devops_engineer`, `researcher` — are seeded by
`bootstrap_role_model_policies_if_needed` **with an empty `preferred` on purpose**. This is a product
decision, not an oversight:

- At bootstrap time nothing is known about which models each configured endpoint actually exposes.
  Seeding invented pairs would produce candidates that do not exist, and a pin to a missing model is
  worse than no pin: it degrades to tier 2 anyway, but silently and with a misleading policy.
- Without a pin the router still applies every hard filter (transport, cost ceiling, privacy, quota,
  context window) and then picks the best-scoring candidate available today. A hand-written pin ages
  badly: it survives provider catalog changes and keeps routing a role to a model that is no longer
  the best — or no longer offered.
- What the seeded policy *does* fix is the part that must never be implicit: the cost ceiling, the
  token ceiling and the approval threshold per role. Before this seed, a role without a policy fell
  back silently to `developer`'s limits.

To pin a model for a role, use **Model Gateway → Policies** (or `PATCH
/api/v1/model-gateway/role-policies/{id}`) and add the `{provider, model}` pair to `preferred`. Use
the provider-level wildcard `{"provider": "...", "model": "*"}` when the intent is "any model from
this provider", which is what the provider wizard and `scripts/setup_omniroute.py` write for
auto-routed gateways.

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
