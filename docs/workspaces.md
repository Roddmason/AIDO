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

- Capture dirty state and diff refs before archive.
- Link workspace ownership to agent runs and job runs.
- Add devcontainer metadata without making Docker mandatory.
