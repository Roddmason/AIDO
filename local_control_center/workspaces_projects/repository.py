from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.store import utc_now

from .git_worktrees import create_git_worktree


ACTIVE_WORKSPACE_STATUSES = {"allocated", "preparing", "ready", "locked", "running", "dirty"}


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if fallback is None else fallback


def row_to_workspace(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "taskId": row["task_id"],
        "ownerAgentId": row["owner_agent_id"],
        "path": row["path"],
        "status": row["status"],
        "isolationType": row["isolation_type"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "archivedAt": row["archived_at"],
    }


class WorkspaceConflictError(RuntimeError):
    pass


class WorkspacesRepository:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root

    def allocate_workspace(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_id: str,
        reason: str = "",
        isolation_type: str = "directory",
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
        base_branch: str = "HEAD",
    ) -> dict[str, Any]:
        placeholders = ",".join("?" for _ in ACTIVE_WORKSPACE_STATUSES)
        existing = self.connection.execute(
            f"""
            SELECT * FROM workspaces
            WHERE project_id = ? AND task_id = ? AND status IN ({placeholders})
            LIMIT 1
            """,
            (project_id, task_id, *sorted(ACTIVE_WORKSPACE_STATUSES)),
        ).fetchone()
        if existing:
            raise WorkspaceConflictError(f"Task {task_id} already has active workspace {existing['id']}")

        workspace_id = f"workspace-{uuid.uuid4()}"
        workspace_path = self.root / ".tmp" / "workspaces" / workspace_id
        resolved_isolation = "directory"
        metadata: dict[str, Any] = {"reason": reason}
        if isolation_type == "git_worktree":
            repo_path = self._project_path(project_id)
            result = create_git_worktree(
                repo_path=repo_path,
                worktree_path=workspace_path,
                task_id=task_id,
                workspace_id=workspace_id,
                base_branch=base_branch,
            )
            metadata["gitWorktree"] = result
            if result["status"] == "created":
                resolved_isolation = "git_worktree"
            else:
                workspace_path.mkdir(parents=True, exist_ok=True)
        else:
            workspace_path.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT INTO workspaces
                (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
                 metadata, created_at, updated_at, archived_at, workflow_run_id, workflow_step_id)
            VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                workspace_id,
                project_id,
                task_id,
                agent_id,
                str(workspace_path),
                resolved_isolation,
                json_dumps(metadata),
                timestamp,
                timestamp,
                workflow_run_id,
                workflow_step_id,
            ),
        )
        if resolved_isolation == "git_worktree":
            self.connection.execute(
                """
                INSERT INTO git_branches
                    (id, workspace_id, project_id, branch_name, base_branch, status, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    f"git-branch-{uuid.uuid4()}",
                    workspace_id,
                    project_id,
                    metadata["gitWorktree"]["branchName"],
                    metadata["gitWorktree"].get("baseBranch", base_branch),
                    json_dumps(metadata["gitWorktree"]),
                    timestamp,
                    timestamp,
                ),
            )
        self.connection.execute(
            """
            INSERT INTO workspace_allocations
                (id, workspace_id, project_id, task_id, agent_id, status, reason, created_at, released_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL)
            """,
            (f"workspace-allocation-{uuid.uuid4()}", workspace_id, project_id, task_id, agent_id, reason, timestamp),
        )
        return self.get_workspace(workspace_id)

    def _project_path(self, project_id: str) -> Path:
        row = self.connection.execute("SELECT path FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise KeyError(f"Project not found: {project_id}")
        return Path(row["path"])

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not row:
            raise KeyError(f"Workspace not found: {workspace_id}")
        return row_to_workspace(row)

    def list_workspaces(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM workspaces WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workspaces ORDER BY updated_at DESC").fetchall()
        return [row_to_workspace(row) for row in rows]

    def list_workspaces_for_workflow(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        if not workflow_run_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_run_ids)
        rows = self.connection.execute(
            f"SELECT * FROM workspaces WHERE workflow_run_id IN ({placeholders}) ORDER BY updated_at DESC",
            tuple(workflow_run_ids),
        ).fetchall()
        return [row_to_workspace(row) for row in rows]

    def archive_workspace(self, workspace_id: str, *, reason: str = "") -> dict[str, Any]:
        self.get_workspace(workspace_id)
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE workspaces
            SET status = 'archived', updated_at = ?, archived_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, workspace_id),
        )
        self.connection.execute(
            """
            UPDATE workspace_allocations
            SET status = 'released', released_at = ?
            WHERE workspace_id = ? AND status = 'active'
            """,
            (timestamp, workspace_id),
        )
        return self.get_workspace(workspace_id)
