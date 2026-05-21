# Credentials

## What It Does

AIDO does not store provider API keys in SQLite, code, logs, prompts, snapshots
or model gateway payloads. Provider accounts store only a `credentialRef`.
Runtime code resolves that reference immediately before a real provider call.

For real provider keys, prefer an external OpenBao/Vault-compatible secrets
service. Environment variables remain supported for development and bootstrap
tokens, but they are not the recommended place to keep long-lived model API
keys.

## Supported Refs

- `env:NAME`: resolves `NAME` from the process environment.
- `keyring:service/account`: optional local OS/keyring-backed lookup when the
  Python `keyring` package and a compatible backend are installed outside core.
- `openbao:mount/path#field`: resolves a field from a remote OpenBao KV v2
  secret using the Vault-compatible HTTP API.
- `vault:mount/path#field`: compatibility alias for Vault-compatible services.

Bare legacy environment names are normalized to `env:NAME` only when they look
like existing project env vars, for example `NVIDIA_NIM_API_KEY`. New
configuration should always use an explicit prefix.

## Recommended Paths

1. Mock/no real calls: keep `AIDO_ENABLE_REAL_PROVIDER_CALLS=false`; no
   provider secrets are needed.
2. Fast local development: use `env:NAME`, knowing it is less auditable and
   weaker against local environment/process inspection.
3. Real provider usage: use `openbao:mount/path#field` backed by remote OpenBao
   or a Vault-compatible service.

Environment variables are acceptable for quick local development:

```powershell
$env:NVIDIA_NIM_API_KEY = "<real key outside repo>"
```

For a real setup, store the provider key in OpenBao or a compatible remote
service and keep only the ref in AIDO:

```json
{
  "credentialRef": "openbao:secret/providers/nvidia_nim#api_key"
}
```

The resolver reads KV v2 from:

```text
GET {AIDO_SECRET_VAULT_ADDR}/v1/{mount}/data/{path}
```

For the example above, that is:

```text
GET https://vault.example/v1/secret/data/providers/nvidia_nim
```

and extracts `data.data.api_key`.

## Vault Configuration

Configure the remote secret endpoint with:

```powershell
$env:AIDO_SECRET_VAULT_ADDR = "https://vault.example"
$env:AIDO_SECRET_VAULT_AUTH_METHOD = "approle"
$env:AIDO_SECRET_VAULT_ROLE_ID_REF = "env:AIDO_OPENBAO_ROLE_ID"
$env:AIDO_SECRET_VAULT_SECRET_ID_REF = "keyring:aido/openbao-secret-id"
```

AppRole is the recommended simple bootstrap for real usage. AIDO posts the
resolved `role_id` and `secret_id` to:

```text
POST {AIDO_SECRET_VAULT_ADDR}/v1/{AIDO_SECRET_VAULT_APPROLE_PATH}/login
```

and uses the returned short-lived token only in memory for the KV v2 lookup.
`AIDO_SECRET_VAULT_APPROLE_PATH` defaults to `auth/approle`.

Token auth remains supported for development, migration and emergency
bootstrap:

```powershell
$env:AIDO_SECRET_VAULT_AUTH_METHOD = "token"
$env:AIDO_SECRET_VAULT_TOKEN_REF = "keyring:aido/openbao-token"
```

`AIDO_SECRET_VAULT_TOKEN_REF` may point to `keyring:` or `env:`. As a fallback,
the resolver also checks `AIDO_SECRET_VAULT_TOKEN`, `OPENBAO_TOKEN`, and
`VAULT_TOKEN`.

AppRole bootstrap refs may point to `env:` or `keyring:`. They may not point to
`openbao:` or `vault:` because that would require a vault token to retrieve the
credentials needed to get a vault token. Keep `secret_id` short-lived or wrapped
when your OpenBao deployment supports that flow; never commit it to the repo.

Vault addresses must use `https://`. `http://127.0.0.1`, `http://localhost` and
`http://[::1]` are allowed only for explicit development with
`AIDO_ALLOW_INSECURE_LOCAL_VAULT=true`. Redirects are not followed when fetching
secrets.

This does not eliminate secret-zero. Nothing can: the local process still needs
some identity to authenticate to the remote service. The security improvement is
that long-lived model provider keys are centralized in the remote secrets
manager, where they can be audited, rotated, revoked, and scoped separately
from the AIDO process.

## Endpoints

Credential refs are configured through provider accounts:

- `GET /api/v1/model-gateway/providers`
- `POST /api/v1/model-gateway/providers`
- `PATCH /api/v1/model-gateway/providers/{id}`
- `POST /api/v1/model-gateway/route/execute`

## Safety Rules

- Raw values such as `sk-...`, `Bearer ...`, `api_key=...`, `secret=...` or
  `token=...` are rejected as invalid `credentialRef` values.
- Public API responses expose only credential status: `configured`, `unverified`,
  `missing`, `unknown`, `invalid`, `unsupported`, or `unavailable`.
- Health checks and route execution must not include secret values in messages,
  audit payloads, usage records or raw provider responses.
- Real provider calls still require `AIDO_ENABLE_REAL_PROVIDER_CALLS=true` plus
  policy, budget and quota clearance.
- Provider listing and health checks validate ref shape and remote vault
  configuration without fetching secret values. Remote/keyring refs can appear
  as `unverified` until execution time, when the actual secret value is fetched.

## Testing

```powershell
uv run pytest tests_py/test_model_runtime_gateway.py -q
corepack pnpm@10.24.0 run security:secrets
```

## Risks And Limitations

- `keyring:` is intentionally optional. The core project does not depend on a
  vault service or a fair-code secret manager.
- HashiCorp Vault-compatible services can be used as external infrastructure,
  but AIDO does not embed HashiCorp Vault or depend on its client packages as
  core runtime. OpenBao-compatible KV v2 is the preferred open-source path.
- Only KV v2 payloads with `data.data` are accepted for `openbao:`/`vault:`
  refs.
- AIDO does not migrate secrets from environment variables into a local store.
  That would create plaintext persistence risk unless a trusted external
  adapter is configured.
