# Runtime Providers

`GET /api/v1/runtime/providers` is the runtime truth source for the backend and
frontend. UI surfaces must render these fields directly instead of inferring
availability from provider names or optimistic defaults.

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
- `testOnly`: the provider is useful for tests but must not represent real work.
- `simulationOnly`: the provider can exercise control-plane flow but cannot
  produce a real implementation.
- `reason`: the human-readable reason for unavailable or non-executable state.
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

## Issue To Patch

`issue_to_patch` uses only executable providers with the `issue_to_patch`
capability. The runner allocates a Git worktree workspace, executes through
policy/sandbox, captures diff/log/test artifacts, and links evidence to
workflow, job, agent run, workspace, runtime, artifact IDs, and diff summary.

The runtime state must be visible in Command Center, Agents, Model Gateway,
Workflows, Evidence, and Runtime Providers views. A simulation-only or
unavailable runtime can produce diagnostic evidence, but it cannot set a real
workflow to `completed`.
