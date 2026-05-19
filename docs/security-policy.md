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

## Command Classification

`security_policy/command_classifier.py` classifies commands into categories and
risk levels.

Critical examples:

- recursive forced deletes
- privilege escalation
- force push
- production deploys
- secret writes/removals

Low-risk examples:

- test commands
- build commands
- read-only inspection commands

## Current Limits

The evaluator is now workspace-aware when the request references an allocated
workspace. Low-risk shell commands are allowed only when the requested path is
inside the workspace root. The same command outside that root is downgraded to
`requires_approval` with `path_outside_workspace`.

Git worktree commands are routed through `security_policy/git_command_runner.py`
as an allowlisted internal helper. Feature modules must not call `subprocess`
directly.

The classifier is intentionally conservative. It is still not a sandbox. The
next hardening step is command-argument allowlisting for writes, installs,
network access, and test/build commands that can execute arbitrary hooks.
