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

- `internal_mock`: implemented and used by tests.
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

## Model Gateway

`agents/model_gateway.py` resolves model policies before any provider adapter is
allowed to run. The current implementation:

- selects the first policy-allowed preferred/fallback provider;
- honors `allowRemote` and `allowLocal`;
- blocks calls that would exceed `maxCostUsd`;
- redacts secret-looking metadata before persistence;
- records `model_calls` and `cost_usage`.

LiteLLM/OpenAI/Ollama execution adapters should attach behind this contract, not
replace it.

## Structured Output

Agent runs should return structured JSON with verdict, summary, evidence
references, risks, and next actions. Free-form text is insufficient for the
control plane.

OpenHands and SWE-agent are optional adapters, not core dependencies. Their
status is surfaced through the integrations API, and any future execution must
remain behind the tool broker, policy engine, isolated workspace, and evidence
capture. They must not edit the primary working tree directly.

Each optional code runtime now exposes a versioned execution contract. Contract
version 1 supports `version_check` and `issue_to_patch`, requires structured
`argv`, requires a workspace path, and requires `issueText` for issue-to-patch
runs. Dangerous runtime flags such as `--no-sandbox`, `--privileged`,
`--mount`, `--volume`, and `--network=host` are rejected before install
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

## Runtime Adapter Execution

The broker has executable adapter hooks for:

- `mcp`: registered stdio MCP servers; read-only discovery is allowed when the
  policy allows it, while `tools/call` and other non-read-only operations are
  gated.
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
