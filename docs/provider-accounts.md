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

Environment refs are still supported for local development:

```powershell
$env:NVIDIA_NIM_API_KEY = "<real key outside repo>"
```

Then store `env:NVIDIA_NIM_API_KEY`; do not rely on unprefixed env names in new
configuration.

Only the ref string is stored in SQLite. Raw keys are rejected. Optional refs in
the form `keyring:service/account` can be used for bootstrap material when a
local OS/keyring adapter is installed outside the core runtime.

## Endpoints

- `GET /api/v1/model-gateway/providers`
- `POST /api/v1/model-gateway/providers`
- `PATCH /api/v1/model-gateway/providers/{id}`
- `POST /api/v1/model-gateway/providers/{id}/health-check`
- `POST /api/v1/model-gateway/providers/{id}/discover-models`

## Testing

`test_provider_accounts_crud_endpoints_do_not_expose_raw_credentials` verifies CRUD, metadata persistence and redaction. Provider health tests verify mock-default behavior, fail-closed real checks, 429 cooldown recording and redacted errors.

## Risks

- Health checks run in safe mock mode unless the provider is enabled and
  `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`.
- Model discovery follows the same fail-closed posture. API and gateway
  providers must have a valid credential ref before real discovery can run; the
  system rejects missing credentials before any adapter/network path is invoked.
- Credential status is resolver-based: configured, unverified, missing,
  unknown, invalid, unsupported or unavailable.

## Limitations

- Provider discovery is conservative and mock-backed in tests; exact remote
  model catalogs require explicit real-call opt-in.

## Example

```json
{
  "providerId": "nvidia_nim",
  "credentialRef": "openbao:secret/providers/nvidia_nim#api_key",
  "metadata": { "region": "us", "tier": "trial_rate_limited" },
  "enabled": true
}
```
