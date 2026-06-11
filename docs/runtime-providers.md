# Runtime Providers

`GET /api/v1/runtime/providers` is the runtime truth source for the backend and
frontend. UI surfaces must render these fields directly instead of inferring
availability from provider names or optimistic defaults.

`GET /api/v1/runtime/provider-configuration` is the safe configuration read
model for runtime/model providers. It reads only process environment variables,
does not write SQLite rows, and returns configured/missing state plus
non-reversible fingerprints for configured values. It never returns raw API
keys, endpoint values, model names, or command values.

Required environment variables:

- OpenAI-compatible: `AIDO_OPENAI_COMPATIBLE_BASE_URL`,
  `AIDO_OPENAI_COMPATIBLE_API_KEY`, `AIDO_OPENAI_COMPATIBLE_MODEL`.
- OpenRouter: `AIDO_OPENROUTER_API_KEY`, `AIDO_OPENROUTER_MODEL`.
- NVIDIA NIM / Build: `AIDO_NVIDIA_API_KEY`, `AIDO_NVIDIA_BASE_URL`,
  `AIDO_NVIDIA_MODEL`.
- Anthropic API: `AIDO_ANTHROPIC_API_KEY`, `AIDO_ANTHROPIC_MODEL`.
- Ollama: `AIDO_OLLAMA_BASE_URL`.
- CLI runtimes: `AIDO_CODEX_COMMAND`, `AIDO_CLAUDE_COMMAND`.

Legacy env refs such as `NVIDIA_NIM_API_KEY`, `OPENROUTER_API_KEY`,
`CODEX_CLI_PATH`, and `CLAUDE_CODE_CLI_PATH` remain compatibility inputs for
existing persisted provider accounts, but new runtime configuration should use
the `AIDO_*` names above.

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
$env:AIDO_CODEX_COMMAND = "codex"
$env:AIDO_CLAUDE_COMMAND = "claude"
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
resolvable command, usable version output, enabled account, workspace-safe argv,
and supported capability.

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
  LiteLLM, and Ollama require configured credentials/model or local health
  before being available.
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
| CLI providers | Detectable and executable only after command config, installation, version check, enabled account, `AIDO_ENABLE_CLI_RUNTIMES=true`, and capability support. | Runtime providers API, Command Center runtime picker. | Runtime slice and optional smoke profile tests. | OpenHands/SWE-agent issue-to-patch requires explicit release-smoke argv contracts. |
| Ollama | Configurable through base URL and available only when the daemon responds to `/api/tags`. | Runtime providers API, Model Gateway UI. | Ollama runtime adapter tests. | Missing daemon or model returns unavailable/configuration-required. |
| Manual provider | Persisted as human/manual state. | Runtime providers API. | Internal mock boundary tests. | Not an automated implementation runtime and not executable. |
