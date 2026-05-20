# Budget And Quota Control

## What It Does

Budget rules define cost/token ceilings by scope. Provider limits track RPM, TPM, daily/monthly limits and cooldowns. `QuotaManager` blocks providers in cooldown before routing/execution.

## Configuration

Seeds include global monthly and role task-level defaults. Edit via `/api/v1/model-gateway/budget-rules`.

## Endpoints

- `GET/POST/PATCH /api/v1/model-gateway/budget-rules`
- `GET/PATCH /api/v1/model-gateway/provider-limits`

## Testing

`test_quota_manager_blocks_provider_in_cooldown` verifies 429 cooldown blocks routing.

## Risks

- Provider-reported quotas are not implemented for all adapters.
- Unknown limits use `conservative` strategy by default.

## Limitations

- Sliding-window counters are schema-ready but the MVP primarily enforces cooldown and request-size checks.

## Example

NVIDIA NIM 429 errors call `record_rate_limit()` and set `cooldown_until`.
