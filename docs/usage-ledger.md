# Usage Ledger

## What It Does

`usage_ledger` records API and CLI usage with input, cached input, output, reasoning and tool token counts plus estimated and actual cost fields.

## Configuration

No credential is required. Providers and runtimes call `UsageLedger.record_usage()` after mock or real execution.

## Endpoints

- `GET /api/v1/model-gateway/usage-ledger`
- `GET /api/v1/model-gateway/usage-ledger/summary`

## Testing

`test_usage_ledger_records_estimated_and_actual_usage` verifies estimated usage keeps `actualCostUsd = null` and provider usage can record actual cost.

## Risks

- Providers without exact usage must mark `rawUsage.usage_source = "estimated"`.
- Existing `cost_usage` is updated for compatibility, but detailed analysis should use `usage_ledger`.

## Limitations

- The ledger does not reconcile provider invoices; `actualCostUsd` is only filled when a provider/runtime reports enough data.

## Example

```json
{
  "providerId": "nvidia_nim",
  "model": "auto_best_available",
  "runtimeType": "api",
  "rawUsage": { "usage_source": "estimated" }
}
```
