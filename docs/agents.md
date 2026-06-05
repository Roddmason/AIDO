# Agents

Agents are modeled as contracts, not personalities.

## Implemented Foundation

- `agent_profiles`: role, runtime type, tools, skills, permission profile, and
  quality gates.
- `agent_runs`: structured run input/output.
- `agent_tool_calls`: tool-call trace records.
- `model_calls`: model-call trace records.
- `cost_usage`: local cost ledger.
- `agents/tool_broker.py`: policy-gated tool-call broker for runtime adapters.
- `skills` and `skill_versions`: versionable skill registry loaded from local
  `SKILL.md` files.

## Runtime Types

- `api`: provider/API runtime mode; adapter execution is gated and not direct.
- `cli`: CLI runtime mode; tool calls are brokered, policy-recorded, and not
  shell-executed directly by the agent API.
- `ollama`: local model runtime mode; detection uses the local HTTP API.
- `hybrid`: mixed mode for profiles that may use API, CLI, or local models.
- `manual`: human/manual runtime mode.

Optional runtimes must not break local installation when unavailable.

## Tool Broker Contract

Runtime adapters must submit tool calls as structured records:

```json
{
  "tool": "shell",
  "command": "uv run pytest tests_py -q",
  "path": "H:\\Proyectos\\...",
  "workspaceId": "workspace-..."
}
```

The broker evaluates the action through `security_policy.policy_engine`, records
`permission_decisions`, creates `action_requests` when a job-linked tool call
needs approval, and persists `agent_tool_calls`.

Agent profiles now enforce `allowedTools` at the broker boundary. A profile that
does not list `mcp`, `openhands`, `swe_agent`, or `shell` cannot invoke that
tool even if the policy engine would otherwise allow the payload.

Allowed shell execution is available only through the restricted subprocess
sandbox. The adapter requires `execute: true` plus structured `argv`; it never
falls back to a command string or `shell=True`. Non-allowlisted executables,
missing argv, non-zero exits, timeouts, and paths outside the workspace are
recorded in the tool-call payload and fail the agent run.

## Runtime Adapter Contract

`agents/runtime_adapters.py` defines the typed runtime adapter port:
`RuntimeAdapter`, `RuntimeExecutionRequest`, `RuntimeExecutionResult`, and
`RuntimeAdapterRegistry`.

`RuntimeExecutionRequest` carries the execution context (`projectId`,
`workflowRunId`, `workflowStepId`, `jobId`, `agentRunId`, `workspaceId`,
`workspacePath`), capability, structured `argv`, input payload, timeout,
`approvalGrantId`, and metadata. Productive subprocess execution rejects command
strings, empty argv, non-string argv entries, non-allowlisted executables,
dangerous sandbox flags, and working directories outside `workspacePath`.

`RuntimeExecutionResult` is the only adapter outcome contract. It records
`status`, `exitCode`, stdout/stderr/output artifact IDs, evidence package ID,
started/completed timestamps, technical `reason`, and whether output was
redacted. `completed` means the real adapter actually ran and produced real
evidence; missing credentials, endpoints, binaries, grants, health checks, or
workspace support must return `configuration_required`, `unavailable`, or
`blocked`.

Built-in adapters are deliberately narrow:

- `RestrictedSubprocessAdapter`: executes structured argv with `shell=False`,
  cwd inside the workspace, real timeout, redacted output, and artifact-backed
  large stdout/stderr.
- `CliVersionAdapter`: allows only `version_check` for Codex, Claude,
  OpenHands, and SWE-agent executables. It does not perform code-edit or
  workflow execution.
- `OllamaAdapter`: uses the real Ollama HTTP API and fails closed when base URL,
  daemon health, model, or messages are missing.
- `OpenAICompatibleAdapter`: uses real OpenAI-compatible HTTP calls only when
  base URL, API key, model, and `AIDO_ENABLE_REAL_PROVIDER_CALLS=true` are set.

## Model Gateway

`agents/model_gateway.py` owns model planning, execution gating, provider health,
usage recording and redaction before any provider adapter is allowed to run. The
current implementation:

- selects the first policy-allowed preferred/fallback provider;
- honors `allowRemote` and `allowLocal`;
- blocks calls that would exceed `maxCostUsd` or the explicit remaining budget;
- returns `configuration_required`, `blocked` or `unavailable` instead of
  simulating execution when credentials, gates or endpoints are not ready;
- redacts secret-looking metadata before persistence through the shared
  `local_control_center/shared/redaction.py` policy;
- records legacy `model_calls` for compatibility and detailed `usage_ledger`
  rows only from real provider/runtime usage or explicit blocked/unavailable
  outcomes.

LiteLLM/OpenAI-compatible/Ollama execution adapters attach behind this contract,
not around it.

Agent profiles also carry the Unified Model & Runtime Gateway controls:

- `routingProfileId` and `roleModelPolicyId` select the route policy used by the agent.
- `allowedProviders` and `allowedRuntimes` constrain the catalog before execution.
- `maxTokensPerRun`, `allowRemote`, `allowCli`, `allowApi`, and `requiresApprovalOverUsd` are persisted with the profile and surfaced in the Agents UI.
- These fields are separate from the legacy `modelPolicyId`; mixing them would blur role-based routing with direct model-call policy.

## Structured Output

Agent runs should return structured JSON with verdict, summary, evidence
references, risks, and next actions. Free-form text is insufficient for the
control plane.

## DeveloperAgent

DeveloperAgent is a real implementation agent contract, not a generic profile
persona. Its public contract is available through
`GET /api/v1/agents/developer/status` and the runtime providers response under
`developerAgent`.

The contract requires:

- input: `projectId`, `workspaceId`, `taskId`, `instruction`, optional
  `preferredRuntime`, `qaCommands`, `requireApproval`, `maxCostUsd`,
  `approvalGrantId`, and model metadata;
- output: status, runtime result, QA results, diff summary, workspace, job,
  agent run, and evidence package;
- tools: `shell`, `openai_compatible`, `ollama`, and `workspace_patch`;
- runtime capability: CLI `code_edit` or model `chat` followed by structured
  patch application;
- allocated workspace and linked evidence.

CLI execution supports configured Codex CLI and Claude CLI. The runner builds
their argv through `runtime_registry.py` and executes only through
`ToolBroker`, never through `CliRuntime.run()`.

Ollama and OpenAI-compatible execution are real model calls, but model text is
not treated as a completed implementation. The model must return valid JSON
with files to write; the `workspace_patch` adapter validates and applies those
files inside the workspace. Invalid JSON, missing files, unavailable health,
missing model/credential, disabled real-provider calls, or provider execution
errors return blocked, configuration-required, unavailable, or failed state
with a technical reason.

OpenHands and SWE-agent are optional adapters, not core dependencies. Their
status is surfaced through the integrations API, and any future execution must
remain behind the tool broker, policy engine, isolated workspace, and evidence
capture. They must not edit the primary working tree directly.

Test-only simulators are not registered runtime types, are not returned by the
runtime provider API, and must not be used to mark a real workflow as completed.

Each optional code runtime now exposes a versioned execution contract. Contract
version 1 supports `version_check` and `issue_to_patch`, requires structured
`argv`, requires a workspace path, and requires `issueText` for issue-to-patch
runs. Dangerous runtime flags such as `--no-sandbox`, `--privileged`,
`--mount`, `--volume`, `--network=host`, and split `--network host` are rejected before install
detection or subprocess execution. This prevents an unavailable local runtime
from hiding malformed or unsafe adapter payloads.

The optional runtime smoke script runs version checks automatically when a
runtime CLI is detected. Deeper `issue_to_patch` smoke is intentionally
release-profile only: set `AIDO_RUNTIME_ISSUE_TO_PATCH_SMOKE=1` plus
`AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON` or
`AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON` with the exact installed CLI syntax.
The script still submits the run through the agent profile, tool broker,
policy, sandbox, and evidence path; it never launches those runtimes directly.

For release validation runners, set `AIDO_RUNTIME_RELEASE_VALIDATION=1`.
That mode forces `issue_to_patch` smoke on and fails early unless both runtime
argv variables and their matching issue text variables are supplied:
`AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON`,
`AIDO_OPENHANDS_ISSUE_TEXT`, `AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON`, and
`AIDO_SWE_AGENT_ISSUE_TEXT`. The first argv element must resolve to an
installed command or an existing executable path on the runner.

Release validation runners should export those environment variables and run
`local-control-center/scripts/smoke-runtime-adapters.ps1 -PreflightOnly` before
starting the control center. That preflight emits a JSON report per adapter and
fails before server startup if any required argv, issue text, or executable is
missing. The repo does not ship a GitHub quality workflow; optional runtime
validation remains an explicit local or release-runner action.

## Runtime Adapter Execution

The broker has executable adapter hooks for:

- `mcp`: registered stdio MCP servers; read-only discovery is allowed when the
  policy allows it, while `tools/call` and other non-read-only operations are
  gated.
- `ollama` and `openai_compatible`: real model adapter calls. DeveloperAgent
  may use them only with structured patch output and subsequent
  `workspace_patch` application.
- `workspace_patch`: applies structured file content inside the allocated
  workspace after policy approval; it rejects traversal, absolute paths,
  symlinks, secret paths, and oversized patches.
- `openhands`: optional CLI/package detection; execution requires structured
  `argv` for an OpenHands command and runs through the restricted subprocess
  sandbox.
- `swe_agent`: optional CLI/package detection; execution requires structured
  `argv` for a SWE-agent command and runs through the restricted subprocess
  sandbox.

Adapters are not exposed as public execution endpoints. The accepted path is:

```text
agent run input -> tool broker -> allowedTools -> policy decision -> approval/grant if needed -> adapter -> tool-call record -> evidence
```

This is intentionally stricter than "runtime is installed, therefore execute".
