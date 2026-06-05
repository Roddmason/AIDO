# Provider Accounts

## What It Does

`provider_accounts` stores provider configuration without secrets: provider type, API format, base URL, credential reference, enabled state, quota mode, health and redacted structured `metadata`.

## Configuration

Use credential refs for credentials. External OpenBao/Vault-compatible refs are
preferred for real provider keys:

```json
{
  "credentialRef": "openbao:secret/providers/nvidia_nim#api_key"
}
```

Configure OpenBao authentication outside provider accounts. For real usage,
prefer AppRole bootstrap (`AIDO_SECRET_VAULT_AUTH_METHOD=approle`) so provider
keys stay in the remote secret manager and the local process receives only a
scoped session token in memory.

Environment refs are still supported for local development. Prefer the runtime
configuration variables documented in `docs/runtime-providers.md`; the safe
configuration endpoint reads those variables directly and does not persist them
in SQLite.

```powershell
$env:AIDO_NVIDIA_API_KEY = "<real key outside repo>"
$env:AIDO_NVIDIA_BASE_URL = "<provider endpoint>"
$env:AIDO_NVIDIA_MODEL = "<provider model>"
```

If you also create a provider account, store only a ref such as
`env:AIDO_NVIDIA_API_KEY`; do not store raw keys or raw env values.

Only the ref string is stored in SQLite. Raw keys are rejected. Optional refs in
the form `keyring:service/account` can be used for bootstrap material when a
local OS/keyring adapter is installed outside the core runtime.

## Endpoints

- `GET /api/v1/model-gateway/providers`
- `POST /api/v1/model-gateway/providers`
- `PATCH /api/v1/model-gateway/providers/{id}`
- `POST /api/v1/model-gateway/providers/{id}/health-check`
- `POST /api/v1/model-gateway/providers/{id}/discover-models`
- `GET /api/v1/runtime/provider-configuration`

## Testing

`test_provider_accounts_crud_endpoints_do_not_expose_raw_credentials` verifies CRUD, metadata persistence and redaction. Provider health tests verify fail-closed checks, explicit real-call opt-in, 429 cooldown recording and redacted errors.

## Risks

- Health checks return `configuration_required`, `misconfigured`, or `blocked`
  instead of simulating provider success when credentials, enablement, or
  real-call opt-in are missing.
- Model discovery follows the same fail-closed posture. API and gateway
  providers must have a valid credential ref before real discovery can run; the
  system rejects missing credentials before any adapter/network path is invoked.
- Credential status is resolver-based: configured, unverified, missing,
  unknown, invalid, unsupported or unavailable.

## Limitations

- Provider discovery requires explicit real-call opt-in for remote providers.
  Unit tests may monkeypatch adapters to isolate network, but product API
  responses cannot expose mock-backed model catalogs.

## Example

```json
{
  "providerId": "nvidia_nim",
  "credentialRef": "openbao:secret/providers/nvidia_nim#api_key",
  "metadata": { "region": "us", "tier": "trial_rate_limited" },
  "enabled": true
}
```
