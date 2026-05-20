# Workspaces

Workspaces are task-scoped execution areas. The control plane must not let two
agents edit the same working tree for the same task.

## Implemented Foundation

- `POST /api/v1/workspaces` allocates one active workspace per
  `projectId + taskId`.
- `POST /api/v1/workspaces/{id}/archive` marks the workspace archived and
  releases the active allocation.
- Workspaces can carry `workflowRunId` and `workflowStepId` for UI traceability.
- `isolationType: git_worktree` creates a Git worktree when the project path is
  a Git repository.
- If Git is unavailable or the project is not a repo, the request degrades to a
  normal directory workspace with explicit `metadata.gitWorktree.status`.
- Archiving a real Git worktree calls the allowlisted Git runner to remove that
  worktree and marks the `git_branches` row archived.
- Archiving captures evidence before cleanup: Git worktrees include a bounded
  `git_diff` reference, large patches are promoted to artifacts, and every
  workspace includes a `workspace_snapshot` diff reference.
- Workspace ownership is explicit through `ownerAgentId`, `taskId`,
  `workflowRunId`, and `workflowStepId`; jobs and agent runs use the same
  workflow run/step IDs for UI traceability without sharing a working tree.
- Workspace allocation accepts optional `devcontainer` metadata with
  `enabled`, `templateId`, `image`, and `features`. The control plane stores it
  as `metadata.devcontainer.status=metadata_only`; it does not require Docker
  or start containers during MVP allocation.

## Current Git Modes

- `created`: Git worktree and branch were created.
- `degraded_not_git_repo`: project path is not a Git repository.
- `degraded_git_unavailable`: Git CLI is not available.
- `degraded_worktree_failed`: Git returned an error; stderr is truncated in
  metadata for diagnosis.

## Security Rule

Policy evaluation uses the allocated workspace path when a request includes
`workspaceId`. Low-risk shell actions outside that path require approval.

## Next Steps

- Add devcontainer execution only after sandbox policy, image catalog review,
  evidence capture, and explicit approval semantics are defined.
