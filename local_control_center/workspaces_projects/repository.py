"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

from .cleanup import capture_workspace_snapshot
from .git_worktrees import create_git_worktree, is_git_repository, remove_git_worktree

ACTIVE_WORKSPACE_STATUSES = {"allocated", "preparing", "ready", "locked", "running", "dirty"}
COPY_IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "test-results",
}
COPY_MAX_FILES = 5000
COPY_MAX_BYTES = 100 * 1024 * 1024


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


class WorkspaceIsolationError(RuntimeError):
    pass


def _ignored_for_copy(relative: Path) -> bool:
    return any(part in COPY_IGNORED_PARTS for part in relative.parts)


def _copy_project_source(source_path: Path, workspace_path: Path) -> dict[str, Any]:
    source = source_path.resolve(strict=False)
    if not source.exists() or not source.is_dir():
        raise WorkspaceIsolationError("Project path must exist before allocating an isolated workspace.")
    workspace_path.mkdir(parents=True, exist_ok=True)
    files_copied = 0
    bytes_copied = 0
    skipped_symlinks = 0
    for candidate in sorted(source.rglob("*")):
        relative = candidate.relative_to(source)
        if _ignored_for_copy(relative):
            continue
        if candidate.is_symlink():
            skipped_symlinks += 1
            continue
        if not candidate.is_file():
            continue
        size = candidate.stat().st_size
        if files_copied + 1 > COPY_MAX_FILES:
            raise WorkspaceIsolationError(f"Workspace copy exceeds file limit: {COPY_MAX_FILES}")
        if bytes_copied + size > COPY_MAX_BYTES:
            raise WorkspaceIsolationError(f"Workspace copy exceeds byte limit: {COPY_MAX_BYTES}")
        target = workspace_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        files_copied += 1
        bytes_copied += size
    return {
        "status": "copied",
        "filesCopied": files_copied,
        "bytesCopied": bytes_copied,
        "skippedSymlinks": skipped_symlinks,
    }


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
        branch_name: str | None = None,
        devcontainer: dict[str, Any] | None = None,
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
        project_path = self._project_path(project_id)
        if devcontainer:
            metadata["devcontainer"] = {**devcontainer, "status": "metadata_only"}
        timestamp = utc_now()
        if is_git_repository(project_path):
            result = create_git_worktree(
                repo_path=project_path,
                worktree_path=workspace_path,
                task_id=task_id,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
            )
            metadata["gitWorktree"] = result
            if result["status"] != "created":
                raise WorkspaceIsolationError(f"Git worktree creation failed: {result['status']}")
            resolved_isolation = "git_worktree"
        else:
            if isolation_type == "git_worktree":
                metadata["gitWorktree"] = {"status": "degraded_not_git_repo"}
            metadata["sourceCopy"] = _copy_project_source(project_path, workspace_path)
        metadata["workspaceManifest"] = self._write_workspace_manifest(
            workspace_id=workspace_id,
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            source_path=project_path,
            workspace_path=workspace_path,
            isolation_type=resolved_isolation,
            created_at=timestamp,
            git_worktree=metadata.get("gitWorktree"),
            source_copy=metadata.get("sourceCopy"),
        )
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
            (
                f"workspace-allocation-{uuid.uuid4()}",
                workspace_id,
                project_id,
                task_id,
                agent_id,
                reason,
                timestamp,
            ),
        )
        return self.get_workspace(workspace_id)

    def _write_workspace_manifest(
        self,
        *,
        workspace_id: str,
        project_id: str,
        task_id: str,
        agent_id: str,
        source_path: Path,
        workspace_path: Path,
        isolation_type: str,
        created_at: str,
        git_worktree: dict[str, Any] | None,
        source_copy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        branch = (git_worktree or {}).get("branchName")
        source_commit = (git_worktree or {}).get("sourceCommit")
        manifest = {
            "kind": "workspace_manifest",
            "version": 1,
            "workspaceId": workspace_id,
            "projectId": project_id,
            "taskId": task_id,
            "ownerAgentId": agent_id,
            "sourcePath": str(source_path),
            "workspacePath": str(workspace_path),
            "isolationType": isolation_type,
            "createdAt": created_at,
            "sourceCommit": source_commit,
            "sourceBranch": (git_worktree or {}).get("sourceBranch"),
            "branch": branch,
            "limits": {
                "maxFiles": COPY_MAX_FILES,
                "maxBytes": COPY_MAX_BYTES,
                "ignoredParts": sorted(COPY_IGNORED_PARTS),
            },
            "gitWorktree": git_worktree,
            "sourceCopy": source_copy,
            "fileManifest": capture_workspace_snapshot(workspace_path),
        }
        manifest_path = self.root / ".tmp" / "workspace-manifests" / f"{workspace_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest["manifestPath"] = str(manifest_path)
        manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
        return manifest

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
        workspace = self.get_workspace(workspace_id)
        timestamp = utc_now()
        metadata = dict(workspace["metadata"] or {})
        if reason:
            metadata["archiveReason"] = reason
        if workspace["isolationType"] == "git_worktree":
            cleanup = remove_git_worktree(
                repo_path=self._project_path(workspace["projectId"]),
                worktree_path=Path(workspace["path"]),
            )
            metadata["gitWorktreeCleanup"] = cleanup
            if cleanup["status"] == "removed":
                self.connection.execute(
                    """
                    UPDATE git_branches
                    SET status = 'archived', updated_at = ?
                    WHERE workspace_id = ?
                    """,
                    (timestamp, workspace_id),
                )
        self.connection.execute(
            """
            UPDATE workspaces
            SET status = 'archived', updated_at = ?, archived_at = ?, metadata = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, json_dumps(metadata), workspace_id),
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
