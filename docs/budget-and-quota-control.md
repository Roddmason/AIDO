# Budget And Quota Control

## What It Does

Budget rules define cost/token ceilings by scope. Provider limits track RPM, TPM, daily/monthly limits and cooldowns. `BudgetRuleEvaluator` and `QuotaManager` run before route selection and return explicit `budgetResult` and `quotaResult` payloads.

## Configuration

Seeds include global monthly and role task-level defaults. Edit via `/api/v1/model-gateway/budget-rules`. Supported budget actions are `deny`, `require_approval`, `fallback` and `warn`.

## Endpoints

- `GET/POST/PATCH /api/v1/model-gateway/budget-rules`
- `GET/PATCH /api/v1/model-gateway/provider-limits`

## Testing

Tests verify cooldown blocking, budget denial, budget approval gating and route preview result payloads.

## Risks

- Provider-reported quotas are not implemented for all adapters.
- Unknown limits use `conservative` strategy by default.

## Limitations

- Sliding-window counters are basic and local. They enforce cooldown, request-size and current-window token/request ceilings when `current_window_json` includes counters.

## Example

NVIDIA NIM 429 errors call `record_rate_limit()` and set `cooldown_until`.
