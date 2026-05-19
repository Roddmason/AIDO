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

The classifier is intentionally conservative. It is not a sandbox. The next
hardening step is path-aware allowlisting so low-risk commands are only allowed
inside an allocated workspace when they can mutate outputs.
