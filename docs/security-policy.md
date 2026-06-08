# Security Policy

The policy system is deterministic first. OPA or external policy engines can be
added later, but the local MVP must be auditable without network services.

## Decisions

- `allow`
- `requires_approval`
- `requires_human`
- `deny`

## Inputs

The evaluator accepts tool, command, path, role, project, workspace, git
operation, deployment target, network requirement, and secrets requirement.

Operational security data is redacted through
`local_control_center/shared/redaction.py` before persistence. This includes
agent runs, tool calls, jobs, action requests, policy decisions, permission
grants, model calls, test metadata, evidence logs, and audit/event payloads.
Patch contents and screenshot binaries are preserved as evidence payloads by
default; surrounding metadata is still sanitized.

## Command Classification

`security_policy/command_classifier.py` classifies commands into categories and
risk levels. Argument-level shell allowlists live in
`security_policy/permissions.py`; low-risk commands must match the executable
and expected arguments, not merely contain a safe-looking substring.

Critical examples:

- recursive forced deletes
- privilege escalation
- force push
- production deploys
- secret writes/removals

Low-risk examples:

- test commands
- build commands
- lint commands
- interpreter version diagnostics
- read-only inspection commands

Package-manager installs, unknown package scripts, and lifecycle hooks such as
`preinstall`, `postinstall`, `prepare`, `publish`, `deploy`, and `release` are
not low-risk. They require approval even when requested by a `dev_safe` agent.

## Permission Profiles

- `plan`: shell denied. Use for planning, discovery, review, and read-only
  context preparation.
- `dev_safe`: allows allowlisted test/build/lint/read-only commands inside the
  workspace. Installs, network actions, git writes, and unknown shell commands
  require approval.
- `qa`: allows allowlisted tests, build, typecheck, lint, diagnostics, and
  read-only inspection inside the workspace. Unknown package scripts, installs,
  network actions, and shell commands outside the QA allowlist require
  approval.
- `release`: shell requires approval by default; production deploy remains
  human-required.

The evaluator uses explicit `permissionProfile` first, then role-based defaults.

## Current Limits

Every productive runtime or tool execution must enter through this chain:

```text
ToolBroker -> PolicyEngine -> Approval/Grant -> RuntimeAdapter -> Evidence
```

Agent implementation workflows must allocate an isolated workspace before any
executable tool call is evaluated. Git projects use a real `git worktree` and
task branch under `.tmp/workspaces`; non-Git projects are copied into an
isolated directory with explicit file and byte limits. The project root is
source material only and must not be used as an execution workspace.

Each workspace allocation records a manifest containing `workspaceId`,
`projectId`, `taskId`, `ownerAgentId`, `workspacePath`, `sourcePath`,
`createdAt`, source commit, source branch, task branch, copy/worktree details,
limits, and a file manifest with hashes. Workflow evidence must include final
Git status/diff data, the workspace manifest, and a workspace file snapshot
with hashes before cleanup. Git worktree cleanup may remove the temporary
working directory, but it must not delete the persisted evidence package,
manifest, artifacts, or audit events.

Feature modules, workflows, agent executors, model gateways, evidence
collectors, adapters, jobs, and UI-facing providers must not invoke CLI,
subprocess, Docker, MCP, or runtime tools directly. If a real brokered adapter
or grant is missing, the request must return `unavailable`,
`configuration_required`, or `blocked` with a technical reason. It must not
report completion through mock, demo, sample, fake, dummy, placeholder, or
hardcoded success behavior.

The broker boundary blocks:

- `shell=True`
- command string execution
- missing or non-structured `argv`
- missing or unknown `workspaceId` for executable tool calls
- cwd outside the workspace
- requested `workspacePath` that differs from the registered workspace
- any executable `path` outside the registered workspace
- dangerous flags such as `--no-sandbox`, `--privileged`, `--mount`,
  `--volume`, `--network=host`, and split-token `--network host`
- network host mode unless explicitly allowlisted by policy
- privileged containers
- arbitrary mounts

Direct subprocess use is limited to approved policy/runtime boundary modules:
`security_policy/sandbox.py` for the restricted subprocess and Docker sandbox,
and `security_policy/git_command_runner.py` for internal git worktree commands.
Workflow code must call `ToolBroker`; runtime adapters may call sandbox
primitives only after the broker and policy stages have already accepted the
request.

The evaluator is now workspace-aware when the request references an allocated
workspace. Executable tool calls without a registered workspace are denied
before policy approval and before any runtime adapter is invoked. Low-risk shell
commands are allowed only when the requested path is inside the workspace root.
Requests outside that root are denied, not converted into approval prompts.

`issue_to_patch` runtime execution is a named policy operation, not a generic
shell bypass. The runner may submit `operation=issue_to_patch_runtime` only
after selecting a configured executable runtime, allocating a workspace, and
building structured `argv`. The policy allows that operation only for the
`aido_issue_to_patch_runner` agent, with `workflowKind=issue_to_patch`,
`runtimeId`, a `dev_safe` profile, and a registered workspace. Networked or
secret-bearing runtime execution still requires approval.

DeveloperAgent uses named operations as well:

- `developer_agent_runtime`: Codex or Claude CLI execution only, for the
  `developer_agent` profile, with `runtimeId`, `agentRunId`, `dev_safe`, and a
  registered workspace. The runner builds CLI argv through
  `agents/runtime_registry.py` and then submits it to `ToolBroker`.
- `developer_agent_model_call`: real Ollama or OpenAI-compatible adapter calls.
  Ollama must pass local health and provide a model. OpenAI-compatible calls
  require real provider configuration, explicit health, a configured model, and
  `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`; adapter credentials remain provider
  configuration and are not exposed to the agent prompt.
- `developer_agent_patch_apply`: applies only structured model output through
  the `workspace_patch` adapter. Paths must be relative to the workspace, cannot
  traverse outside it, and cannot target secret or credential paths.
- `developer_agent_qa`: legacy scoped operation for developer-local QA.
  Productive implementation workflows should use `qa_agent_command` instead.

QAAgent uses `qa_agent_command` for all productive QA execution. The operation
is allowed only for `agentId=qa_agent`, `permissionProfile=qa`, a registered
workspace, an agent run audit id, `tool=shell`, and a low-risk QA command
category. Unknown, missing, or non-allowlisted commands are not converted into
passed verdicts; they remain gated, skipped with a technical reason, or failed
according to command criticity.

DevOpsAgent uses `devops_agent_command` for brokered local validation commands.
The operation is allowed only for `agentId=devops_agent`,
`permissionProfile=qa`, a registered workspace, an agent run audit id,
`tool=shell`, and low-risk build, lint, typecheck, test, diagnostic, or
read-only categories. Missing build scripts are recorded as
`skipped_with_reason` with a technical reason; they are not converted into
passed evidence. Docker is optional. A Docker healthcheck may run only through
the broker and active sandbox policy; absent Docker records skipped evidence
instead of failing startup.

ArchitectAgent uses `architect_agent_model_call` for architecture review model
execution. The operation is allowed only for `agentId=architect_agent`,
`permissionProfile=plan`, a registered workspace, an agent run audit id, and
`tool/runtimeId` in `ollama` or `openai_compatible`. The agent has no shell
tool and cannot request secret-bearing execution. If no configured executable
model runtime exists, the run returns `runtime_unavailable`; it does not issue
an architectural approval. ADR and risk records are created only after the real
runtime output validates against the ArchitectAgent schema and every finding,
risk, required change, and approval recommendation cites input diff/test
evidence.

SecurityAgent uses deterministic controls for security review. It scans the
allocated workspace and optional diff artifact for secret-like tokens, path
traversal inputs, dangerous command flags, dependency file integrity, and
recorded policy violations. The verdict is computed only from those controls:
secrets, traversal, denied policy decisions, or critical Docker flags block the
run; non-critical findings produce risk; a clean scan passes.

The optional `security_agent_model_call` operation is secondary analysis only.
It is allowed only for `agentId=security_agent`, `permissionProfile=qa`, a
registered workspace, an agent run audit id, and matching `tool/runtimeId` in
`ollama` or `openai_compatible`. The model call cannot request secrets and
cannot override the deterministic verdict or replace the findings JSON,
scanned-file list, hashes, and evidence package.

DeveloperAgent completion is forbidden unless runtime execution was real, the
workspace diff is non-empty, QAAgent command evidence passed, and an evidence
package is linked. A chat response without a valid structured patch is blocked;
it is not treated as an implementation.

Git worktree commands are routed through `security_policy/git_command_runner.py`
as an allowlisted internal helper. Feature modules must not call `subprocess`
directly.

Allowed low-risk shell tool calls can execute through
`security_policy.sandbox.RestrictedSubprocessSandbox`. This degraded local
sandbox uses `shell=False`, requires structured `argv`, allowlists executable
names, caps timeout/output, and rejects working directories outside the
workspace. It is deliberately less capable than Docker isolation and should only
run low-risk commands that already passed policy.

Runtime execution also rejects known dangerous flags before subprocess or Docker
invocation, including `--no-sandbox`, `--privileged`, `--mount`, `--volume`,
`--network=host`, and split-token `--network host`. A command string is never
accepted as an execution fallback.

Sensitive tool calls have a stricter flow:

```text
action_request -> approval with reason -> permission_grant -> one execution -> consumed
```

Approving an action no longer acts as a broad job-level bypass. The approval API
requires a non-empty reason and creates a one-use `permission_grant` tied to the
project, job, action request, agent profile, tool, command, structured `argv`,
workspace, runtime, and path. The action request also carries risk level,
workspace/runtime context, diff refs, evidence refs, the policy reason, and an
expiration timestamp so the operator can review concrete context before
deciding.

A later tool call must provide `approvalGrantId`; the broker validates the
stored scope and expiration before execution. Reused, expired, mismatched,
missing, or revoked grants are denied and recorded as policy decisions. A
mismatch does not consume the grant. Rejecting an action also requires a
non-empty human reason and moves the job to `cancelled`; a rejected request
cannot be approved later.

Active grants can be revoked through:

```text
POST /api/v1/permissions/grants/{grantId}/revoke
```

The endpoint requires the local write token and a non-empty reason. Revocation
records `revokedAt`, `revokedBy`, `revokeReason`, an operational event, and an
audit entry. Revoked grants cannot be consumed by the broker.

`GET /api/v1/sandbox/status` reports Docker and restricted-subprocess posture.
Docker is optional for the local MVP. The Docker adapter builds a locked-down
command plan (`--read-only`, read-only workspace bind mount, tmpfs `/tmp`) and
uses the active `sandbox_profiles` row for allowed images, allowed network
modes, default network, memory, CPU, and timeout. The default profile keeps
network disabled with `none`, but the allowlist now lives in SQLite policy
configuration instead of the tool broker.

Docker tool calls require a configured sandbox profile, catalog image,
structured `argv`, and allocated workspace path. Unknown images, missing argv,
inactive profiles, or network modes outside the profile allowlist are blocked
before Docker is invoked. The broker records the permission decision,
permission grant, sandbox profile, sandbox result, and an evidence package
reference on the agent run. Captured execution metadata is redacted before
persistence.

Sandbox profiles can be revoked through:

```text
POST /api/v1/sandbox/profiles/{profileId}/revoke
```

The endpoint requires the local write token and a non-empty reason. Revoking a
profile moves it out of `active`, records audit/event evidence, and blocks
subsequent Docker execution that references the profile.

Sandbox profiles can be edited through:

```text
PATCH /api/v1/sandbox/profiles/{profileId}
```

Edits require the local write token and a non-empty reason. The endpoint
validates catalog image strings, Docker resource values, timeout limits, and
keeps MVP network mode locked to `none`. Each edit records an operational event
and an audit payload containing the previous and updated profile snapshots. It
also writes a `policy_revisions` record with subject, version, reason, actor,
previous snapshot, updated snapshot, and changed-field list. The dashboard uses
those revision records to render policy diffs without raw JSON editing or direct
SQLite inspection. Broader network access must be modeled as a separate policy
decision rather than a quiet profile change.

The classifier is intentionally conservative. It is still not a sandbox. Expired
referenced evidence uses explicit reviewed `export` or `delete` actions through
the retention endpoints.

## Real Capability Table

| Capability | Real state | Endpoint/UI | Tests | Limitations |
| --- | --- | --- | --- | --- |
| Deterministic policy evaluation | Implemented for shell/tool/runtime operations with allow, approval, human-required, and deny outcomes. | `/api/v1/policies/evaluate`, policy decisions in overview and Policy & Security UI. | Security policy and execution boundary tests. | The classifier is not itself a sandbox; execution still needs broker and sandbox/adapters. |
| Tool broker execution boundary | Implemented for structured tool calls, profile allowed-tools, policy decisions, grants, adapters, and evidence. | Agent run APIs, jobs/approvals APIs. | `tests_py/test_execution_boundary_architecture.py`, agent tests. | Direct feature-module subprocess execution is outside the product contract. |
| Restricted subprocess sandbox | Implemented for allowlisted low-risk argv with `shell=False`, workspace cwd containment, timeouts, and redacted output. | Brokered shell tool calls. | Sandbox and Semgrep guardrail tests. | Less isolated than Docker; reserved for low-risk commands already allowed by policy. |
| Docker sandbox | Implemented as optional catalog/profile-gated adapter. | `/api/v1/sandbox/status`, sandbox profile APIs, Policy & Security UI. | Sandbox profile and permission grant tests. | Docker is not required for startup; unknown images/network modes are blocked. |
| One-use permission grants | Implemented with approval reason, scoped grant, expiration, consumption, and revocation. | Jobs & Approvals UI, `/api/v1/permissions/grants/{grantId}/revoke`. | Approval/grant tests. | A broad job approval is not enough; later tool calls must present the matching grant. |
| Productive truth scanner | Implemented in local quality gate. | `scripts/productive-truth-scan.py`, `pnpm run quality:productive-truth`. | `tests_py/test_no_mock_productive_scanner.py`. | Docs/tests are allowed to discuss prohibited terms; productive runtime/API/UI code is not. |
