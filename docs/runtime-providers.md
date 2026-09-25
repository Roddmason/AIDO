# Runtime Providers

`GET /api/v1/runtime/providers` is the runtime truth source for the backend and
frontend. UI surfaces must render these fields directly instead of inferring
availability from provider names or optimistic defaults.

`GET /api/v1/runtime/provider-configuration` is the safe configuration read
model for runtime/model providers. It reads only process environment variables,
does not write SQLite rows, and returns configured/missing state plus
non-reversible fingerprints for configured values. It never returns raw API
keys, endpoint values, model names, or command values.

Runtime configuration sources:

- OpenAI-compatible: `AIDO_OPENAI_COMPATIBLE_BASE_URL`,
  `AIDO_OPENAI_COMPATIBLE_API_KEY`, `AIDO_OPENAI_COMPATIBLE_MODEL`.
- OpenRouter: `AIDO_OPENROUTER_API_KEY`, `AIDO_OPENROUTER_MODEL`.
- NVIDIA NIM / Build: `AIDO_NVIDIA_API_KEY`, `AIDO_NVIDIA_BASE_URL`,
  `AIDO_NVIDIA_MODEL`.
- Anthropic API: `AIDO_ANTHROPIC_API_KEY`, `AIDO_ANTHROPIC_MODEL`.
- Ollama: `AIDO_OLLAMA_BASE_URL`.
- Local OpenAI-compatible servers (llama.cpp, LM Studio, vLLM or any local
  server): no environment variables. They are persisted provider accounts
  created from the catalog or `POST /api/v1/local-endpoints`; see
  [Local Runtimes](#local-runtimes).
- CLI runtimes: executable paths and native CLI accounts live in
  `runtime_installations` and `runtime_accounts`. `AIDO_CODEX_COMMAND` and
  `AIDO_CLAUDE_COMMAND` are deprecated bootstrap/CI overrides only.

Legacy env refs such as `NVIDIA_NIM_API_KEY`, `OPENROUTER_API_KEY`,
`CODEX_CLI_PATH`, and `CLAUDE_CODE_CLI_PATH` remain compatibility inputs for
existing persisted provider accounts or CI smoke profiles, but new runtime
configuration should be persisted instead of treated as process environment.

## Local Configuration Examples

Set variables before starting the control center. Then inspect
`/api/v1/runtime/provider-configuration` and `/api/v1/runtime/providers`.

```powershell
$env:AIDO_OPENAI_COMPATIBLE_BASE_URL = "https://provider.example/v1"
$env:AIDO_OPENAI_COMPATIBLE_API_KEY = "<real API key>"
$env:AIDO_OPENAI_COMPATIBLE_MODEL = "provider/model"
$env:AIDO_OPENROUTER_API_KEY = "<real API key>"
$env:AIDO_OPENROUTER_MODEL = "provider/model"
$env:AIDO_NVIDIA_API_KEY = "<real API key>"
$env:AIDO_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
$env:AIDO_NVIDIA_MODEL = "provider/model"
$env:AIDO_ANTHROPIC_API_KEY = "<real API key>"
$env:AIDO_ANTHROPIC_MODEL = "claude-model-id"
$env:AIDO_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
```

API execution also requires:

```powershell
$env:AIDO_ENABLE_REAL_PROVIDER_CALLS = "true"
```

CLI execution also requires:

```powershell
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
```

These flags do not force readiness. API providers still need credentials,
model, enabled account, and explicit healthy status. CLI providers still need a
persisted or PATH-resolvable command, usable version output, enabled native CLI
account, workspace-safe argv, and supported capability.

## Provider State

Each provider record exposes:

- `detected`: local executable or daemon/remote provider was actually detected by
  a safe check.
- `configured`: required credentials, endpoint, local binary, or operator
  configuration exists.
- `available`: the provider health check or local detection path is usable.
- `executable`: the provider can run productive work through policy and an
  isolated workspace.
- `requiredConfiguration`: the concrete config fields required before the
  provider can become configured.
- `reason`: the human-readable reason for unavailable or non-executable state.
- `healthStatus`: persisted provider health state such as `unknown`,
  `healthy`, `offline`, `degraded`, or `misconfigured`.
- `healthCheckedAt`: timestamp of the latest explicit health check. Env vars do
  not populate this field.
- `lastError`: sanitized technical health failure reason. It must not contain
  API keys, bearer tokens, endpoints with tokens, or raw secrets.
- `capabilities`: versioned runtime capabilities such as `version_check` or
  `issue_to_patch`.
- safety metadata: argv, workspace, sandbox, approval, network, and capability
  constraints relevant to execution.

Configured is not the same as detected, available, or executable. API providers
fail closed when credentials, model configuration, or explicit health are
missing. CLI providers may be detected for diagnostics while still blocked for
productive workflow execution.

Routing must consume the same truth model. A provider account and model catalog
seed are not sufficient for selection: non-manual providers must be enabled,
healthy, and have a recorded health check timestamp before productive routing.
The legacy manual provider is optional human state, not automated availability.

Remote provider health is not called by `/api/v1/runtime/providers`. That
endpoint reports persisted truth only. Health calls happen only through the
explicit Model Gateway health-check action, and failed checks persist
`lastHealthCheckAt` plus a sanitized reason.

`developerAgent` is a derived readiness record in the same response. It is not
persisted on `agent_profiles`; it is computed from configured provider status,
capabilities, health, and workspace/evidence requirements. CLI runtimes require
`code_edit`. Ollama/OpenAI-compatible require real model execution plus
structured patch application before they can produce workspace changes.

## Adapter Execution Truth

Runtime execution is a port-and-adapter contract, not a product demo path.
`RuntimeAdapterRegistry` returns `unavailable` when no real adapter is
registered. Registered adapters must report:

- `configuration_required` when required env, credential, model, endpoint, CLI
  command, or workspace configuration is missing.
- `unavailable` when a configured real provider or executable cannot pass its
  health/version check.
- `blocked` when policy, argv shape, cwd containment, capability, or approval
  constraints reject execution.
- `timed_out`, `failed`, or `completed` only after a real attempted execution.

No runtime adapter may expose mock, fake, dummy, placeholder, sample, demo, or
hardcoded success behavior in product code. Test doubles belong only in unit
tests and must not be registered in provider status, workflow execution, model
gateway, runtime registry, evidence, jobs, API, UI, or adapters.

## Built-In Providers

- `manual`: operator/manual path, useful for approval and human state, not an
  automated patch generator.
- API providers such as OpenAI-compatible, OpenRouter, NVIDIA NIM, Anthropic,
  and LiteLLM require configured credentials and a model before being available.
- Local model runtimes (Ollama and the OpenAI-compatible servers listed in
  [Local Runtimes](#local-runtimes)) require an enabled account, an explicit
  healthy health check and a validated model; a bearer credential is optional.
- CLI providers such as Codex CLI, Claude Code CLI, OpenHands, and SWE-agent
  require `shutil.which` detection, a safe `--version` health check, structured
  argv, workspace boundary checks, and capability support before productive
  execution.

### NVIDIA NIM Usage And Cost

NVIDIA NIM is treated as an OpenAI-compatible API provider for execution, but
its cost and token accounting must remain conservative:

- `estimate_cost()` returns `estimatedCostUsd = null` unless a real pricing
  source is configured elsewhere. Unknown trial/free-tier status is not a zero
  cost.
- Response text is not token evidence. If the provider response does not include
  a `usage` object with token fields, token counts stay `0`, `tokenStatus` is
  `unknown`, and `usageSource` is `unknown`.
- Token counts are `actual` only when NVIDIA returns provider usage fields such
  as `prompt_tokens`, `completion_tokens`, or `total_tokens`.
- Catalog seeds must not mark NVIDIA NIM as `freeTier=true` or set token prices
  to `0.0` unless a real pricing snapshot documents that state.

### Anthropic Usage And Cost

Anthropic API execution uses the real Anthropic Messages API:

- Health check and discovery call `GET /v1/models` with `x-api-key` and
  `anthropic-version`.
- Chat execution calls `POST /v1/messages`; system messages are sent in the
  Anthropic `system` field and user/assistant messages in `messages`.
- Runtime configuration requires `AIDO_ANTHROPIC_API_KEY` and
  `AIDO_ANTHROPIC_MODEL`. Without both, provider status is
  `configuration_required`; without a successful health check and
  `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`, it is not executable.
- Usage is `actual` only when the response contains provider `usage` fields
  such as `input_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, or `output_tokens`.
- If Anthropic returns text without usage, token counts stay `0`,
  `tokenStatus` is `unknown`, and `usageSource` is `unknown`.
- `estimate_cost()` returns `estimatedCostUsd = null`; costs are recorded only
  when an external pricing catalog/snapshot supplies real pricing for the
  selected model.

## Local Runtimes

AIDO treats every OpenAI-compatible inference server running on the operator's
machine as a first-class local runtime. They share the `openai_compatible`
protocol family and `OpenAICompatibleProvider`; each catalog entry declares a
`LocalRuntimeProfile` that tells AIDO how to probe liveness and read model load
state. AIDO never starts, stops, loads or unloads server processes or models.

| Catalog id | Server | Default base URL | Liveness | Model load state | Multi-model | Cold start budget |
| --- | --- | --- | --- | --- | --- | --- |
| `llama_cpp` | llama.cpp `llama-server` | `http://127.0.0.1:8082/v1` | `/health` | `openai_models_status` | router with autoload | 180 s |
| `lm_studio` | LM Studio | `http://127.0.0.1:1234/v1` | `/v1/models` | `lm_studio_rest` | JIT + Auto-Evict | 180 s |
| `vllm` | vLLM (WSL2) | `http://127.0.0.1:8000/v1` | `/health` | `single_model` | one process per model | 60 s |
| `local_openai_compatible` | Any local OpenAI-compatible server | none (required) | `/v1/models` | `none` | unknown | 180 s |

Load state is read defensively and never persisted (a shared 10 s cache; a slow
or unknown answer reads as `unknown`):

- `openai_models_status`: `GET /v1/models` → `data[].status.value`
  (`loaded`, `loading`, `unloaded`) is the only load-state source. This field
  is observed on llama.cpp builds but is not part of its official
  documentation. `GET /props` is read only by discovery, to recognize a
  llama.cpp server; neither load state nor the health probe reads it.
- `lm_studio_rest`: `GET /api/v1/models` (`loaded_instances`), falling back to
  `GET /api/v0/models` (`state == "loaded"`) before LM Studio 0.4.0.
- `single_model`: the only id listed by `/v1/models` is loaded.
- `none`: load state is always `unknown`; selection uses the default model.

### Health, validation and model selection

- Health check: `GET <liveness>` with a short timeout and, when the profile
  requires it, `GET /v1/models` listing the expected model. A `503` or a
  `loading` model reports `model_loading`, which is not a failure: it does not
  open the 300 s cooldown and records no failed receipt in
  `model_execution_health`. A server that does not answer fails with
  `local_server_unreachable`.
- Model sync first checks the profile's liveness and fails with
  `local_server_unreachable` instead of reporting zero models; the models it
  discovers are enabled by default (the setup wizard lets the operator untick
  them).
- Per-model configuration lives in `local_model_settings` (default model,
  `code_edit`/`code_review` opt-ins, operator order and whether the model
  validated `json_schema` output), separate from `model_catalog`, which a resync
  rewrites. A local runtime's capabilities are `chat` plus the capabilities of
  its enabled models; runtime-wide `code_edit`/`code_review` seeding no longer
  applies to local entries.
- Validation runs per (account, model): a real chat call plus JSON output against
  a minimal schema, with `max_tokens` 1024 and the profile's reasoning-off hint
  when it has one. A model that is still loading ends as `deferred` with reason
  `model_loading`, and a validation that cannot get the account's concurrency
  slot ends as `deferred` with reason `local_endpoint_busy`; neither is `failed`
  nor invalidates the current validation. The thread-team seal gate (30 min) and
  execution gate (24 h + fingerprint) evaluate the sealed model, not only the
  provider.
- Deterministic selection (every local account, Ollama included): among the
  enabled, validated models with the role's capabilities AIDO prefers a loaded
  model, then the model another role of the same run already uses, then the
  default model, then operator order. Picking a model that is not loaded relies
  on the server's autoload and records a `local_model_switch` event
  (`runtimeId`, `fromModel`, `toModel`, `role`, `reason`) in the thread and in
  the execution audit, including a failover replacement. Selections made
  outside the product loop (routing previews that `ModelRouter` records) keep
  the switch in `ai_routing_decisions.policyResult.localModelSelections`,
  which is their audit trail. Only one candidate per local account reaches preflight;
  the other models are rejected with `local_model_not_selected`. An empty set
  blocks with `local_model_not_validated`. Thread teams seal the chosen model
  per role in `roleModels`, server side only.

### Locality, privacy and cost

`agents/endpoint_locality.py` is the single source for these rules:

- Locality is `remote` for any `gateway` account or
  `metadata.endpointKind == "remote"`; `loopback` when the base URL host is
  loopback (`localhost`, `127.0.0.0/8`, `::1`, IPv4-mapped); `declared_local`
  when the operator declared a WSL/Docker endpoint through
  `PUT /api/v1/local-endpoints/{provider_id}/declare-local` (private or link-local IP
  literal, or `host.docker.internal`, re-resolved on every use); `remote`
  otherwise.
- `runtime.local.enabled`, `local_private` and `local_only` apply only to local
  accounts whose locality is not `remote`. A llama.cpp server on another LAN
  machine that is not declared is treated as remote for privacy and enablement
  (it needs `runtime.remote.enabled`).
- Cost: self-hosted inference (catalog pricing source `local_runtime_cost_only`,
  `providerType` `local`, not marked remote, host loopback, declared or a
  literal private IP) is zero-cost; model sync marks it `freeTier=true` with
  price 0 and preserves operator overrides. A public URL, a gateway, a
  proxy/tunnel marked remote, or a remote Ollama endpoint created by the Ollama
  router keep the standard unknown-cost path with approval.
- Credentials: a bearer is optional and only by `credentialRef`. AIDO never sends
  a local account's bearer over `http://` to a `remote` host
  (`insecure_credential_transport`); this guard covers `providerType` `local`
  accounts only, `api` and `gateway` accounts keep their current transport rules.
- Local runtimes do not need `AIDO_ENABLE_REAL_PROVIDER_CALLS`: for model calls
  that variable is only an environment override reported as a configuration
  warning, and the SQLite runtime settings below stay authoritative.
- `providerCatalogId` and `localDeclaration` are server-owned fields; a
  client-supplied `providerCatalogId` or `endpointKind: "local"` in metadata is
  ignored.

### Settings and project mode

- `runtime.local.enabled` enables every local runtime (seeded from
  `runtime.ollama.enabled` on upgrade). `runtime.ollama.enabled` only restricts
  Ollama: Ollama runs when both are true.
- `runtime.local.maxCallSeconds` caps one local model call (default 900 s, range 30-3600 s).
- `project.runtime.defaultMode` accepts `local` (any enabled local runtime);
  `ollama` stays valid and means `local` restricted to Ollama.

### Resources and concurrency

A call to a server that AIDO does not launch uses the light `local_model_call`
workload class (client reservation only, no GPU, no heavy slot), so a job that
already holds an `agent_cli` lease covers it without a second reservation.
`local_gpu_model` stays reserved for model processes AIDO would launch itself
(none today). Both classes are refused with `unreal_local_gpu_conflict` while
Unreal Editor runs and `resources.blockLocalGpuWhenUnreal` is on, also when the
child borrows its parent's lease. A durable per-account lease (persistent rows
with a wall-clock expiry and a fence) limits concurrent calls (default 1,
`localConcurrencyLimit`); a call that cannot get a slot in time fails with
`local_endpoint_busy`. AIDO does not reserve or measure the
server's VRAM. See `docs/operational-hardening/p0-resource-profiles.md`.

### Model call path

- Local accounts send `{model, messages, temperature, max_tokens, stream: false}`
  plus `response_format` when the model validated `json_schema`; no `metadata`
  and no camelCase aliases. The output limit is sent only to local accounts
  (Ollama keeps `options.num_predict`): remote OpenAI-compatible APIs such as
  OpenAI, Azure OpenAI and OpenRouter never receive `max_tokens`, `maxTokens` or
  `metadata`, because reasoning models there require `max_completion_tokens`.
- Effective timeout = min(request timeout + cold start when switching models,
  `runtime.local.maxCallSeconds`, remaining execution deadline).
- `reasoning_content`, `<think>…</think>` blocks and code fences are stripped
  before parsing. The reasoning-off hint is sent only by the preflight probe
  (`max_tokens` 64; a `finish_reason=length` reply with reasoning counts as
  alive) and by per-model validation; normal agent calls do not send it.
- Agents parse the raw reply from an in-process, read-once transient channel
  keyed by the broker tool-call id, reserved before the call runs (at most 64
  entries, 300 s TTL); artifacts, logs, `agent_tool_calls` and evidence stay
  redacted.
- Provider `usage` and latency reach `usage_ledger` with the real
  `usage_source`; missing usage stays `unknown` with `NULL` tokens.
- A call that fails with `model_loading` or `local_endpoint_busy` is transient:
  neither the gateway nor the tool broker records a failed receipt in
  `model_execution_health`, so it never invalidates the model's validation;
  the other local causes do record one.
- Responses are read with a byte cap.

### Failure causes

| Cause | Meaning | Operator action |
| --- | --- | --- |
| `local_server_unreachable` | Nothing answers at the base URL. | Start the server; for WSL check `.wslconfig` and the firewall. |
| `model_loading` | The server is loading a model (`503` or `loading`). | Wait and retry; it is not a failure. |
| `local_model_load_failed` | The server could not load the requested model. | Check the server log and free memory. |
| `local_auth_required` | The server answered `401`/`403`. | Add a `credentialRef` token. |
| `context_length_exceeded` | The prompt exceeded the model context. | Use a model with a larger context. |
| `insecure_credential_transport` | A local account's bearer would travel over `http://` to a remote host. | Use HTTPS, loopback or a declared local endpoint. |
| `local_endpoint_busy` | The per-account concurrency lease stayed full. | Wait or raise the account's concurrency limit. |
| `insufficient_time_for_model_load` | The execution deadline cannot cover a cold start. | Retry with more time or keep the model loaded. |
| `local_model_not_validated` | No enabled, validated model fits the role. | Validate a model. |
| `local_model_not_selected` | Another model of the same account was chosen. | None; audit only. |

### Status in the API

`GET /api/v1/runtime/providers` adds a `local` list (one `LocalEndpointView` per
local account) and keeps the `ollama` key. `GET /api/v1/runtime/team-candidates`
adds `loadedModels` per candidate and `suggestedRoleModels` to the response.
`loadedModels` is filled only for enabled, profiled accounts whose locality is
not `remote`. When a local cause blocks an account, its `reason` and
`healthReason` start with the exact cause code from the table above; the UI
translates known codes and shows unknown text verbatim. A model call that fails
for a local cause carries `localRuntimeCause` in the agent result and in the
`details` of the product-loop block. The endpoint routes are listed in
`docs/model-gateway.md` (Local endpoints) and the router lives in the
`local_runtimes` package (`docs/backend.md`).

Out of scope: streaming, tool calling, embeddings, explicit load/unload APIs,
Ollama load state (`/api/ps`), Docker Model Runner, SGLang, TGI, Lemonade and
VRAM measurement. LM Studio and vLLM are validated against test doubles built
from their official documentation until a real server is exercised.

## Issue To Patch

`issue_to_patch` uses only executable providers with the `issue_to_patch`
capability. The runner allocates a Git worktree workspace, executes through
policy/sandbox, captures diff/log/test artifacts, and links evidence to
workflow, job, agent run, workspace, runtime, artifact IDs, and diff summary.

## DeveloperAgent

DeveloperAgent selects executable runtimes in this order: Codex CLI, Claude CLI,
OpenAI-compatible, then Ollama. If a preferred runtime is supplied, it must be
catalogued and executable for DeveloperAgent specifically. Generic provider
availability is not enough.

If no runtime is configured, DeveloperAgent returns `runtime_unavailable` with a
technical reason and does not execute. If a model runtime returns chat text
without valid structured patch files, the run is blocked or failed; it cannot
complete from text output alone.

The runtime state must be visible in Command Center, Agents, Model Gateway,
Workflows, Evidence, and Runtime Providers views. An unavailable runtime can
produce blocked diagnostic evidence, but it cannot set a real workflow to
`completed`.

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Provider truth source | Implemented from real configuration, health, detection, enabled state, and execution gates. | `GET /api/v1/runtime/providers`, Runtime & Model Gateway UI. | `tests_py/test_aido_real_runtime_slice.py`, web provider tests. | Provider names and catalog seeds are not readiness. |
| Safe configuration read model | Implemented with configured/missing state and fingerprints. | `GET /api/v1/runtime/provider-configuration`. | Runtime configuration tests. | It never returns raw secrets and does not write credentials. |
| API providers | Configurable and executable only after env/config, health, enabled account, and `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`. | Model Gateway health/configuration surfaces. | Model gateway and agent real-runtime tests. | Disabled by default; missing health or flag returns blocked/unavailable. |
| CLI providers | Detectable and executable only after persisted/PATH command config, installation, version check, enabled native CLI account, `AIDO_ENABLE_CLI_RUNTIMES=true`, and capability support. | Runtime providers API, Command Center runtime picker. | Runtime slice and optional smoke profile tests. | Command env vars are deprecated bootstrap/CI overrides; OpenHands/SWE-agent issue-to-patch requires explicit release-smoke argv contracts. |
| Ollama | Configurable through base URL and available only when the daemon responds to `/api/tags`. | Runtime providers API, Model Gateway UI. | Ollama runtime adapter tests. | Missing daemon or model returns unavailable/configuration-required. |
| Local OpenAI-compatible runtimes | `llama_cpp`, `lm_studio`, `vllm` and `local_openai_compatible` accounts become executable only after an explicit health check; models expose load state and per-model validation. | `/api/v1/local-endpoints`, `/api/v1/local-runtimes/discover`, Settings local endpoints panel, thread runtime team. | `tests_py/test_local_runtimes_docs.py` and the local-runtime suites built on `tests_py/fakes/local_llm_servers.py`. | AIDO never manages server processes; LM Studio and vLLM are validated against test doubles only. |
| Manual provider | Persisted as human/manual state. | Runtime providers API. | Internal mock boundary tests. | Not an automated implementation runtime and not executable. |
