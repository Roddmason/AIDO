# Usage Ledger

## What It Does

`usage_ledger` records API and CLI usage with input, cached input, output, reasoning and tool token counts plus estimated and actual cost fields. `usageSource` is now a first-class field with `actual`, `estimated`, `unavailable`, or `unknown`; `tokenStatus` and `costStatus` make unknown provider usage and pricing explicit. `rawUsage` remains the redacted provider/runtime payload.

## Configuration

No credential is required to record usage. Providers and runtimes call
`UsageLedger.record_usage()` only after real provider/runtime output, explicit
budget/planning estimation, or an unavailable/blocked outcome. Product code must
not use the ledger to record mock execution as completed work. Estimated records
are not evidence of completed model work.

## Endpoints

- `GET /api/v1/model-gateway/usage-ledger`
- `GET /api/v1/model-gateway/usage-ledger/summary`
- `GET /api/v1/model-gateway/benchmarks`
- `GET /api/v1/model-gateway/benchmark-outcomes`
- `POST /api/v1/model-gateway/benchmark-outcomes`

## Testing

`test_usage_ledger_records_estimated_and_actual_usage` verifies estimated usage keeps `actualCostUsd = null`, provider usage can record actual cost and `usageSource` is persisted. Benchmark tests verify usage-derived attempts, outcome-derived success/QA/rework rates, average cost and average latency, including automatic outcome ingestion from evidence linked to `usageLedgerId`.

## Risks

- Providers without exact usage must set `usageSource = "unknown"` for completed
  provider responses without usage, `usageSource = "unavailable"` for blocked or
  unavailable execution, or a non-completed planning state; they must not fabricate token counts. If real
  tokens are reported but pricing is absent, `costStatus = "unknown"` and cost
  fields stay null.
- Existing `cost_usage` is updated only as a temporary read-model for older
  dashboards. New code must use `usage_ledger`; `cost_usage` compatibility is
  scheduled for removal on 2026-09-01.
- Benchmark rows derived only from usage intentionally leave success, QA pass and rework metrics empty. Once `model_benchmark_outcomes` rows exist, those rates are computed as fractions from explicit or evidence-ingested outcomes.

## Limitations

- The ledger does not reconcile provider invoices; `actualCostUsd` is only filled when a provider/runtime reports enough token data and pricing is configured.

## Example

```json
{
  "providerId": "nvidia_nim",
  "model": "auto_best_available",
  "runtimeType": "api",
  "usageSource": "actual",
  "tokenStatus": "actual",
  "costStatus": "unknown",
  "rawUsage": { "usage_source": "provider", "cost_status": "unknown" }
}
```
