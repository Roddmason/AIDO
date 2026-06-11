# Agents

Agents are modeled as contracts, not personalities.

## Implemented Foundation

- `agent_profiles`: role, runtime type, tools, skills, permission profile, and
  quality gates.
- `agent_runs`: structured run input/output.
- `agent_tool_calls`: tool-call trace records.
- `model_calls`: model-call trace records.
- `usage_ledger`: canonical model/runtime usage ledger.
- `cost_usage`: temporary compatibility read-model for older dashboards; new
  code must not write business logic against it.
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

## QAAgent

QAAgent is the command-verification agent. It does not accept model text as QA
evidence and it does not execute commands directly.

Its contract requires:

- input: `projectId`, `workspaceId`, `taskId`, and structured command objects
  with `argv`, optional `label`, `critical`, and `timeoutSeconds`;
- output: `status`, `verdict`, command `results`, workspace, job, agent run,
  and evidence package;
- tools: `shell` only;
- runtime capability: brokered command execution;
- allocated workspace and linked evidence.

When commands are omitted, QAAgent discovers real workspace commands only when
they exist: Python tests, web tests, build, typecheck, and lint scripts. Missing
or non-allowlisted non-critical commands are recorded as
`skipped_with_reason`, never `passed`.

Each command is submitted as `operation=qa_agent_command` through `ToolBroker`.
The policy allows only low-risk QA categories for the `qa_agent` profile.
Verdicts are calculated from real exit codes, execution metadata, stdout/stderr
captures, and artifact hashes. A `passed` text claim is ignored; `completed`
workflow states require executable QA evidence with tool-call IDs and artifact
hashes.

## DevOpsAgent

DevOpsAgent is the local operations validation agent. It validates configuration
files and executes only low-risk local validation commands through the broker.
It does not require Docker and it does not execute scripts directly.

Its contract requires:

- input: `projectId`, `workspaceId`, `taskId`, optional `buildScripts`,
  `dockerHealthcheck`, and metadata;
- output: `status`, `verdict`, command results, versions, config findings,
  scanned files, Docker status, config artifact, workspace, job, agent run, and
  evidence package;
- tools: `shell` only;
- runtime capability: deterministic config checks and brokered command
  execution;
- allocated workspace and linked evidence.

The runner scans PowerShell scripts, Docker files, `package.json`, lockfiles,
and `pyproject.toml` with SHA-256 hashes. It detects deprecated npm/script
patterns, host networking, remote shell piping, invalid package/TOML files,
missing package manager metadata, and local-first violations such as `0.0.0.0`
binds. Product-wide simulation-token enforcement belongs to the local quality
scanner, not to DevOpsAgent runtime execution.

Build scripts are executed only when present in `package.json`, using
structured argv and `operation=devops_agent_command` through `ToolBroker`.
Missing requested build scripts are recorded as `skipped_with_reason` with a
technical reason. Optional Docker health uses the active sandbox profile and is
skipped when Docker is unavailable or not allowed; startup remains healthy
without Docker.

## SecurityAgent

SecurityAgent is the deterministic security review agent. It does not depend on
LLM text for approval and it does not execute commands directly.

Its contract requires:

- input: `projectId`, `workspaceId`, `taskId`, optional `diffArtifactId`,
  structured `commandCandidates`, `pathsToCheck`, `runModelAnalysis`,
  `preferredRuntime`, `approvalGrantId`, and model metadata;
- output: `status`, `verdict`, findings, scanned files, dependency files,
  findings artifact, workspace, job, agent run, and evidence package;
- tools: `openai_compatible` and `ollama` only for optional analysis;
- runtime capability: deterministic local security checks;
- allocated workspace and linked evidence.

The runner scans the allocated workspace and optional diff artifact for
secret-like tokens, records SHA-256 hashes for scanned files, validates
dependency files such as `package.json`, checks explicit path candidates for
traversal outside the workspace, detects dangerous command flags, and includes
recorded policy violations in the findings payload.

Verdict calculation is deterministic: critical secret, traversal, dangerous
Docker, or denied policy findings return `blocked`; non-critical findings
return `risk`; only a clean scan returns `passed`. Findings are written to a
JSON artifact and attached to the evidence package. Optional model analysis is
brokered through `operation=security_agent_model_call` and can only add
secondary context; it cannot replace checks or change the verdict.

## ArchitectAgent

ArchitectAgent is the architecture review agent. It reviews supplied diffs,
workflow context, relevant docs, test results, and the existing risk register
through a configured real model runtime. It does not approve by default and it
does not call providers directly.

Its contract requires:

- input: `projectId`, `workspaceId`, `taskId`, `diffArtifactId`,
  `workflowContext`, optional `relevantDocs`, `testResults`, `riskRegister`,
  `evidenceRefs`, `preferredRuntime`, `approvalGrantId`, and model metadata;
- output: `status`, `runtimeResult`, validated review `output`, workspace,
  job, agent run, evidence package, optional architecture decision, and risk
  entries;
- tools: `openai_compatible` and `ollama` only;
- runtime capability: model `chat`;
- allocated workspace and linked evidence.

The runner submits `operation=architect_agent_model_call` through `ToolBroker`
using a `plan` profile. Policy permits only the `architect_agent` profile with
a registered workspace, agent run audit id, and matching `runtimeId`/tool in
`openai_compatible` or `ollama`. Missing runtime configuration, missing health,
missing model, disabled real-provider calls, or provider errors return
`runtime_unavailable`, `configuration_required`, or `failed` with a technical
reason.

Model output must be valid JSON with `verdict`, `architectureFindings`,
`risks`, `requiredChanges`, `approvalRecommendation`, and `evidenceRefs`.
Every finding, risk, required change, and recommendation must cite refs that
were provided in the input, including the diff artifact or test evidence. ADR
and risk records are persisted only after that validation succeeds; invalid
JSON or ungrounded evidence refs produce `failed_validation` and leave
governance records unchanged.

Each optional code runtime now exposes a versioned execution contract. Contract
version 1 supports `version_check` and `issue_to_patch`, requires structured
`argv`, requires a workspace path, and requires `issueText` for issue-to-patch
runs. Dangerous runtime flags such as `--no-sandbox`, `--privileged`,
`--mount`, `--volume`, `--network=host`, and split `--network host` are rejected before install
detection or subprocess execution. This prevents an unavailable local runtime
from hiding malformed or unsafe adapter payloads.

The optional runtime smoke script runs version checks automatically when a
runtime CLI is detected. Deeper `issue_to_patch` validation is intentionally
release-profile only. OpenHands and SWE-agent now have dedicated opt-in release
validators:

```powershell
$env:AIDO_OPENHANDS_COMMAND = "openhands"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:openhands:release

$env:AIDO_SWE_AGENT_COMMAND = "sweagent"
$env:AIDO_ENABLE_CLI_RUNTIMES = "true"
corepack pnpm@10.24.0 run smoke:swe-agent:release
```

Those validators create a temporary Git repository, run the real
`issue_to_patch` workflow, and fail unless patch, QA, evidence, hashes, and
stdout/stderr artifacts are present. `AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON`
and `AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON` can override argv with explicit
structured JSON. Supported placeholders are `{workspace}`, `{workspace_path}`,
`{prompt}`, `{issue_text}` and `{title}`. Invalid syntax reports
`runtime_unavailable` or release validation failure; it is not converted into a
capability.

The repo does not ship a GitHub quality workflow; optional runtime validation
remains an explicit local or release-runner action.

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

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Agent profile/run storage | Implemented with agent profiles, runs, tool calls, model calls, usage ledger, and skills. | `/api/v1/agent-profiles`, `/api/v1/agent-runs`, Agents UI. | Agent repository/API tests and OpenAPI generation tests. | Profile data does not imply execution readiness; runtime status controls execution. |
| DeveloperAgent | Implemented as a real implementation runner for executable CLI/model runtimes plus workspace patch application. | `/api/v1/agents/developer/status`, `/api/v1/agents/developer/runs`. | `tests_py/test_developer_agent_real_runtime.py`. | Model text alone is not completion; valid structured patch files and QA/evidence are required. |
| QAAgent | Implemented as brokered command verification with verdicts from real exit codes and artifacts. | `/api/v1/agents/qa/runs`, Evidence & QA UI. | QA/evidence tests and issue-to-patch runtime tests. | Missing or non-allowlisted commands are skipped/blocked/failed with reason, not passed. |
| DevOpsAgent | Implemented for deterministic config scans and low-risk brokered validation commands. | `/api/v1/agents/devops/status`, `/api/v1/agents/devops/runs`. | DevOps agent and policy tests. | It does not own no-mock product enforcement; `scripts/productive-truth-scan.py` does. |
| SecurityAgent | Implemented deterministic local security review with optional secondary model analysis. | `/api/v1/agents/security/status`, `/api/v1/agents/security/runs`. | Security agent tests. | Optional model output cannot override deterministic findings or verdict. |
| ArchitectAgent | Implemented for model-backed architecture review with schema and evidence-ref validation. | `/api/v1/agents/architect/status`, `/api/v1/agents/architect/runs`. | `tests_py/test_architect_agent_real_runtime.py`. | Requires configured executable model runtime; no runtime means no approval. |
| Runtime adapters | Implemented for restricted subprocess, CLI version checks, Ollama, OpenAI-compatible calls, workspace patch, MCP/OpenHands/SWE-agent adapter boundaries. | Runtime providers API, agent run APIs. | Runtime adapter contract tests and architecture guardrails. | Optional external adapters require real installed binaries or configured endpoints and remain behind policy/evidence. |
