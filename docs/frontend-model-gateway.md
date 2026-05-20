# Frontend Model Gateway

## What It Does

The React console under `features/model-gateway/ModelGatewayPage.tsx` shows provider accounts, model catalog, routing profiles, role assignments, usage, budgets, limits, routing decisions, CLI sessions, benchmarks and settings.

## Configuration

The page reads `/api/v1/model-gateway/*` through the generated OpenAPI client and uses the local handshake token for mutations.

## Endpoints

The page uses overview, providers, models, routing profiles, role policies, usage ledger, provider limits, budget rules, CLI runtimes, CLI sessions, benchmarks and route preview endpoints.

## Testing

Run:

```powershell
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 run build:web
corepack pnpm@10.24.0 exec playwright test tests_web/control-center.spec.js -g "Model Gateway (console|route preview)"
```

## Risks

- Editing model catalog capabilities/prices from the UI is still basic; backend endpoints support PATCH but the UI currently prioritizes visibility and provider actions.
- Benchmark success/QA/rework metrics show `insufficient data` until outcome collection exists; cost/latency/attempt counts are derived from usage ledger rows.

## Limitations

- The console is data-dense and operational, but deeper inspectors for candidates/rejected policy internals are still table-level summaries.
- Agent profile routing controls live in the Agents page because those fields are part of the executable agent contract.

## Example

The route preview form can prove `developer + implementation + requiresCodeEdit` selects a CLI runtime while `local_private` blocks remote providers.
