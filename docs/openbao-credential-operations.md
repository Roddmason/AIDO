# OpenBao Credential Operations

## What It Does

AIDO uses OpenBao as the recommended remote secrets backend for real provider
credentials. Provider accounts store only refs such as
`openbao:secret/providers/nvidia_nim#api_key`; the raw API key stays in OpenBao.

This path follows the OpenBao Vault-compatible HTTP API:

- AppRole login: `POST /v1/auth/approle/login`
- KV v2 read: `GET /v1/{mount}/data/{path}`

## Recommended Setup

Use a dedicated OpenBao instance outside the local AIDO workspace. Do not commit
tokens, role IDs, secret IDs or provider keys.

Create a KV v2 mount:

```powershell
bao secrets enable -path=secret kv-v2
```

Store provider credentials under provider-specific paths:

```powershell
bao kv put -mount=secret providers/nvidia_nim api_key="<provider key outside repo>"
bao kv put -mount=secret providers/openrouter api_key="<provider key outside repo>"
```

Create a least-privilege read policy:

```hcl
path "secret/data/providers/*" {
  capabilities = ["read"]
}
```

Write the policy:

```powershell
bao policy write aido-model-provider-read .\aido-model-provider-read.hcl
```

Enable AppRole and create a scoped role:

```powershell
bao auth enable approle
bao write auth/approle/role/aido-local-control-center `
  token_policies="aido-model-provider-read" `
  token_ttl="30m" `
  token_max_ttl="2h" `
  secret_id_ttl="24h" `
  secret_id_num_uses=10
```

Fetch bootstrap material outside the repo:

```powershell
bao read -field=role_id auth/approle/role/aido-local-control-center/role-id
bao write -f -field=secret_id auth/approle/role/aido-local-control-center/secret-id
```

For better operations, use response wrapping or your workstation password
manager for `secret_id`. The project supports `env:` and optional `keyring:`
refs for this bootstrap material, but do not store long-lived provider API keys
in local env vars.

## AIDO Configuration

```powershell
$env:AIDO_SECRET_VAULT_ADDR = "https://vault.example"
$env:AIDO_SECRET_VAULT_AUTH_METHOD = "approle"
$env:AIDO_SECRET_VAULT_APPROLE_PATH = "auth/approle"
$env:AIDO_SECRET_VAULT_ROLE_ID_REF = "env:AIDO_OPENBAO_ROLE_ID"
$env:AIDO_SECRET_VAULT_SECRET_ID_REF = "keyring:aido/openbao-secret-id"
```

Then configure provider accounts with refs:

```json
{
  "providerId": "nvidia_nim",
  "credentialRef": "openbao:secret/providers/nvidia_nim#api_key"
}
```

## Preflight

Status-only preflight validates ref shape and bootstrap configuration without
fetching remote provider secrets:

```powershell
corepack pnpm@10.24.0 run security:credentials:preflight -- --ref openbao:secret/providers/nvidia_nim#api_key
```

Fetch preflight performs AppRole login and KV v2 lookup, but still prints only
redacted status:

```powershell
corepack pnpm@10.24.0 run security:credentials:preflight -- --fetch --ref openbao:secret/providers/nvidia_nim#api_key
```

You can also set multiple refs:

```powershell
$env:AIDO_CREDENTIAL_PREFLIGHT_REFS = "openbao:secret/providers/nvidia_nim#api_key,openbao:secret/providers/openrouter#api_key"
corepack pnpm@10.24.0 run security:credentials:preflight -- --fetch
```

## Risks

- AppRole reduces but does not eliminate secret-zero. The local process still
  needs bootstrap identity.
- `role_id` is less sensitive than `secret_id`, but both should be handled as
  operational secrets.
- `secret_id` should be short-lived, limited-use and rotated.
- OpenBao availability becomes a dependency for real provider calls.
- Tests use mocks and do not contact a real OpenBao server.

## Testing

```powershell
uv run pytest tests_py/test_model_runtime_gateway.py -q -k credential_preflight
uv run pytest tests_py/test_project_hygiene_and_license.py -q -k quality_and_security_scripts_are_declared
```
