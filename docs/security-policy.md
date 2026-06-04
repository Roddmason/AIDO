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
- `qa`: allows tests/read-only inspection. Build/write commands require
  approval.
- `release`: shell requires approval by default; production deploy remains
  human-required.

The evaluator uses explicit `permissionProfile` first, then role-based defaults.

## Current Limits

The evaluator is now workspace-aware when the request references an allocated
workspace. Low-risk shell commands are allowed only when the requested path is
inside the workspace root. The same command outside that root is downgraded to
`requires_approval` with `path_outside_workspace`.

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
project, job, action request, agent profile, tool, command, and path. A later
tool call must provide `approvalGrantId`; the broker validates those fields and
atomically consumes the grant before execution. Reused, mismatched, missing, or
revoked grants are denied and recorded as policy decisions.

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

The classifier is intentionally conservative. It is still not a sandbox. The
next hardening step is explicit approved delete/export actions for expired
referenced evidence artifacts.
