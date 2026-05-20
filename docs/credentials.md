# Credentials

## What It Does

AIDO does not store provider API keys in SQLite, code, logs, prompts, snapshots
or model gateway payloads. Provider accounts store only a `credentialRef`.
Runtime code resolves that reference immediately before a real provider call.

## Supported Refs

- `env:NAME`: resolves `NAME` from the process environment.
- `NAME`: legacy shorthand for `env:NAME`.
- `keyring:service/account`: optional local OS/keyring-backed lookup when the
  Python `keyring` package and a compatible backend are installed outside core.

Environment variables are the default and remain the recommended local-first
path for development:

```powershell
$env:NVIDIA_NIM_API_KEY = "<real key outside repo>"
```

Then store only:

```json
{
  "credentialRef": "env:NVIDIA_NIM_API_KEY"
}
```

## Endpoints

Credential refs are configured through provider accounts:

- `GET /api/v1/model-gateway/providers`
- `POST /api/v1/model-gateway/providers`
- `PATCH /api/v1/model-gateway/providers/{id}`
- `POST /api/v1/model-gateway/route/execute`

## Safety Rules

- Raw values such as `sk-...`, `Bearer ...`, `api_key=...`, `secret=...` or
  `token=...` are rejected as invalid `credentialRef` values.
- Public API responses expose only credential status: `configured`, `missing`,
  `unknown`, `invalid`, or `unsupported`.
- Health checks and route execution must not include secret values in messages,
  audit payloads, usage records or raw provider responses.
- Real provider calls still require `AIDO_ENABLE_REAL_PROVIDER_CALLS=true` plus
  policy, budget and quota clearance.

## Testing

```powershell
uv run pytest tests_py/test_model_runtime_gateway.py -q
corepack pnpm@10.24.0 run security:secrets
```

## Risks And Limitations

- `keyring:` is intentionally optional. The core project does not depend on a
  vault service or a fair-code secret manager.
- AIDO does not migrate secrets from environment variables into a local vault.
  That would create plaintext persistence risk unless a trusted OS-backed
  adapter is configured.
- If a deployment needs centralized secret rotation, integrate an external
  vault as an adapter that returns a short-lived environment variable or
  keyring-backed credential reference.
