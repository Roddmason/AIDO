# NVIDIA NIM Provider

## What It Does

`NvidiaNimProvider` is an OpenAI-compatible provider adapter for NVIDIA Build / NIM at `https://integrate.api.nvidia.com/v1`.

## Configuration

- Provider id: `nvidia_nim`
- Credential ref: `NVIDIA_NIM_API_KEY`
- Quota mode: `trial_rate_limited`

## Endpoints

- `POST /api/v1/model-gateway/providers/nvidia_nim/health-check`
- `POST /api/v1/model-gateway/providers/nvidia_nim/discover-models`
- `POST /api/v1/model-gateway/route/preview`

## Testing

`test_nvidia_provider_mock_parses_usage_and_handles_429` verifies mock usage parsing and rate-limit cooldown handling.

## Risks

- NIM is remote; `local_private` blocks it.
- Trial/free limits are unknown until provider-reported data is available. The quota manager uses conservative cooldown on 429.

## Limitations

- Pricing and quota data are not fetched as authoritative truth; catalog rows remain operator-editable.

## Example Uses

Analyst research briefs, summaries, simple classification, backlog triage and QA checklist drafts.
