# Provider Accounts

## What It Does

`provider_accounts` stores provider configuration without secrets: provider type, API format, base URL, credential reference, enabled state, quota mode and health.

## Configuration

Use credential refs for credentials. Environment refs are the default:

```powershell
$env:NVIDIA_NIM_API_KEY = "<real key outside repo>"
```

Only the string `env:NVIDIA_NIM_API_KEY` or legacy shorthand
`NVIDIA_NIM_API_KEY` is stored in SQLite. Optional refs in the form
`keyring:service/account` can be used when a local OS/keyring adapter is
installed outside the core runtime. Raw keys are rejected.

## Endpoints

- `GET /api/v1/model-gateway/providers`
- `POST /api/v1/model-gateway/providers`
- `PATCH /api/v1/model-gateway/providers/{id}`
- `POST /api/v1/model-gateway/providers/{id}/health-check`
- `POST /api/v1/model-gateway/providers/{id}/discover-models`

## Testing

`test_provider_accounts_crud_endpoints_do_not_expose_raw_credentials` verifies CRUD and redaction.

## Risks

- Health checks currently run in safe mock mode from the API route.
- Credential status is resolver-based: configured, missing, unknown, invalid or unsupported.

## Limitations

- Provider discovery is conservative and mock-backed in tests; exact remote model catalogs require real calls.

## Example

```json
{
  "providerId": "nvidia_nim",
  "credentialRef": "env:NVIDIA_NIM_API_KEY",
  "enabled": true
}
```
