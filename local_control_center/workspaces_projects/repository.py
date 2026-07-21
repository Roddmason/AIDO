"""Persistencia transaccional de workspaces: asignación aislada y archivado con allocations.

Coordina el aislamiento físico (git worktree o copia acotada de la fuente), escribe el
manifiesto en disco y registra el estado en SQLite. Las escrituras se agrupan sobre el
``connection`` del caller, que es quien hace commit: ``allocate_workspace`` inserta en
``workspaces`` (+ ``git_branches`` si aplica) y ``workspace_allocations``; ``archive_workspace``
actualiza ``workspaces``, libera ``workspace_allocations`` y archiva la rama git asociada.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

from .cleanup import capture_workspace_snapshot
from .git_worktrees import (
    create_git_worktree,
    delete_git_branch,
    git_available,
    remove_git_worktree,
)

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
EPHEMERAL_PROMPT_WORKSPACE_DIR = ("aido", "prompt-workspaces")


def row_to_workspace(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de ``workspaces`` al dict camelCase del contrato, deserializando metadata."""
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
    """La tarea ya tiene un workspace activo: no se puede asignar otro en paralelo."""


class WorkspaceIsolationError(RuntimeError):
    """No se pudo aislar la fuente del proyecto (ruta inexistente, límites o worktree fallido)."""


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


def _ephemeral_prompt_workspace_root() -> Path:
    temp_root = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    return Path(temp_root, *EPHEMERAL_PROMPT_WORKSPACE_DIR).resolve(strict=False)


def _is_strict_descendant(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


class WorkspacesRepository:
    """Acceso a workspaces sobre SQLite; ``root`` ancla las rutas ``.tmp`` de trabajo y manifiestos."""

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
        reuse_existing: bool = False,
    ) -> dict[str, Any]:
        """Asigna un workspace aislado a la tarea y registra su allocation activa.

        Aísla por git worktree si el proyecto es repositorio git, o por copia acotada de la
        fuente en caso contrario, escribe el manifiesto y, en la misma conexión, inserta el
        workspace (más ``git_branches`` cuando hay worktree) y la allocation; el commit lo hace
        el caller.

        Con ``reuse_existing`` una tarea que ya tiene un workspace activo devuelve ese mismo
        workspace en lugar de fallar: es como el product loop mantiene UNA rama estable por hilo
        entre turnos en vez de crear una ``codex/product-*-<hash>`` por mensaje. El guard de
        conflicto se conserva por defecto (contrato fail-closed de ``issue_to_patch``).

        Raises:
            WorkspaceConflictError: si la tarea ya tiene un workspace activo y ``reuse_existing``
                es falso.
            WorkspaceIsolationError: si la fuente no existe, supera límites o el worktree falla.
        """
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
            if reuse_existing:
                return self.get_workspace(existing["id"])
            raise WorkspaceConflictError(f"Task {task_id} already has active workspace {existing['id']}")

        workspace_id = f"workspace-{uuid.uuid4()}"
        workspace_path = self.root / ".tmp" / "workspaces" / workspace_id
        resolved_isolation = "directory"
        metadata: dict[str, Any] = {"reason": reason}
        project_path = self._project_path(project_id)
        if devcontainer:
            metadata["devcontainer"] = {**devcontainer, "status": "metadata_only"}
        timestamp = utc_now()
        if git_available() and (project_path / ".git").exists():
            result = create_git_worktree(
                repo_path=project_path,
                worktree_path=workspace_path,
                task_id=task_id,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                connection=self.connection,
                root=self.root,
                project_id=project_id,
            )
            if result["status"] == "created":
                metadata["gitWorktree"] = result
                resolved_isolation = "git_worktree"
            elif isolation_type == "git_worktree":
                metadata["gitWorktree"] = result
                raise WorkspaceIsolationError(f"Git worktree creation failed: {result['status']}")
            else:
                metadata["gitWorktree"] = result
                metadata["sourceCopy"] = _copy_project_source(project_path, workspace_path)
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

    def allocate_prompt_workspace(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_id: str,
        source_workspace_id: str,
        reason: str,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
    ) -> dict[str, Any]:
        """Registra un cwd efímero y vacío para un runtime de análisis sin acceso al proyecto.

        La ruta vive bajo ``%TEMP%/aido/prompt-workspaces`` y no copia ningún archivo del
        proyecto. El registro y allocation permiten que ToolBroker aplique el mismo límite de
        workspace que a cualquier otro proceso, mientras el cwd separado impide que el runtime
        descubra ``AGENTS.md`` o skills locales del repositorio por recorrido de ancestros.
        """
        source_workspace = self.get_workspace(source_workspace_id)
        if source_workspace["projectId"] != project_id:
            raise WorkspaceIsolationError("Prompt workspace source must belong to the requested project.")

        project_path = self._project_path(project_id).resolve(strict=False)
        source_path = Path(source_workspace["path"]).resolve(strict=False)
        controlled_root = _ephemeral_prompt_workspace_root()
        if _is_strict_descendant(controlled_root, project_path) or controlled_root == project_path:
            raise WorkspaceIsolationError("Ephemeral prompt workspace root must be outside the project tree.")

        workspace_uuid = str(uuid.uuid4())
        workspace_id = f"workspace-{workspace_uuid}"
        workspace_path = (controlled_root / workspace_uuid).resolve(strict=False)
        if not _is_strict_descendant(workspace_path, controlled_root):
            raise WorkspaceIsolationError("Ephemeral prompt workspace path escaped its controlled root.")
        if workspace_path == source_path or _is_strict_descendant(workspace_path, source_path):
            raise WorkspaceIsolationError(
                "Ephemeral prompt workspace must be outside the source workspace tree."
            )

        controlled_root.mkdir(parents=True, exist_ok=True)
        workspace_path.mkdir(exist_ok=False)
        timestamp = utc_now()
        metadata = {
            "reason": reason,
            "ephemeralPromptWorkspace": {
                "purpose": "product_owner_cli_runtime",
                "controlledRoot": str(controlled_root),
                "sourceWorkspaceId": source_workspace_id,
                "projectInstructionsExcluded": True,
            },
            "workspaceManifest": {
                "kind": "workspace_manifest",
                "version": 1,
                "workspaceId": workspace_id,
                "projectId": project_id,
                "taskId": task_id,
                "ownerAgentId": agent_id,
                "sourcePath": None,
                "workspacePath": str(workspace_path),
                "isolationType": "directory",
                "createdAt": timestamp,
                "promptOnly": True,
                "fileManifest": capture_workspace_snapshot(workspace_path),
            },
        }
        try:
            self.connection.execute(
                """
                INSERT INTO workspaces
                    (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
                     metadata, created_at, updated_at, archived_at, workflow_run_id, workflow_step_id)
                VALUES (?, ?, ?, ?, ?, 'ready', 'directory', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    workspace_id,
                    project_id,
                    task_id,
                    agent_id,
                    str(workspace_path),
                    json_dumps(metadata),
                    timestamp,
                    timestamp,
                    workflow_run_id,
                    workflow_step_id,
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
        except sqlite3.Error:
            self.connection.execute(
                "DELETE FROM workspace_allocations WHERE workspace_id = ?", (workspace_id,)
            )
            self.connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise
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
        """Devuelve el workspace por id.

        Raises:
            KeyError: si no existe ningún workspace con ese id.
        """
        row = self.connection.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not row:
            raise KeyError(f"Workspace not found: {workspace_id}")
        return row_to_workspace(row)

    def list_workspaces(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista workspaces (todos o filtrados por proyecto), ordenados por actualización reciente."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM workspaces WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workspaces ORDER BY updated_at DESC").fetchall()
        return [row_to_workspace(row) for row in rows]

    def list_workspaces_for_workflow(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        """Lista workspaces asociados a las ejecuciones de workflow dadas (vacío si no hay ids)."""
        if not workflow_run_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_run_ids)
        rows = self.connection.execute(
            f"SELECT * FROM workspaces WHERE workflow_run_id IN ({placeholders}) ORDER BY updated_at DESC",
            tuple(workflow_run_ids),
        ).fetchall()
        return [row_to_workspace(row) for row in rows]

    def archive_workspace(
        self, workspace_id: str, *, reason: str = "", delete_branch: bool = False
    ) -> dict[str, Any]:
        """Archiva el workspace, persiste el motivo en metadata y libera su allocation.

        Si está aislado por git worktree intenta removerlo y, solo si la limpieza tuvo éxito,
        marca su rama como ``archived``. Con ``delete_branch`` además borra el ref de la rama de
        trabajo (``git branch -D``) para no acumular ramas ``codex/product-*`` en el repo del
        proyecto; las ramas protegidas (main/master) nunca se borran. En la misma conexión
        actualiza ``workspaces`` (estado, ``archived_at`` y metadata con ``archiveReason``/
        ``gitWorktreeCleanup``/``gitBranchCleanup``) y pasa la allocation activa a ``released``; el
        commit lo hace el caller.

        Raises:
            KeyError: si el workspace no existe.
        """
        workspace = self.get_workspace(workspace_id)
        timestamp = utc_now()
        metadata = dict(workspace["metadata"] or {})
        if reason:
            metadata["archiveReason"] = reason
        prompt_workspace = metadata.get("ephemeralPromptWorkspace")
        if isinstance(prompt_workspace, dict):
            controlled_root = _ephemeral_prompt_workspace_root()
            recorded_root = Path(str(prompt_workspace.get("controlledRoot") or "")).resolve(strict=False)
            workspace_path = Path(workspace["path"]).resolve(strict=False)
            cleanup: dict[str, Any] = {
                "kind": "ephemeral_prompt_workspace_cleanup",
                "controlledRoot": str(controlled_root),
                "path": str(workspace_path),
            }
            if recorded_root != controlled_root:
                cleanup.update(
                    status="refused",
                    reason="Recorded prompt workspace root does not match the controlled temp root.",
                )
            elif not _is_strict_descendant(workspace_path, controlled_root):
                cleanup.update(
                    status="refused",
                    reason="Prompt workspace path is outside the controlled temp root.",
                )
            elif not workspace_path.exists():
                cleanup["status"] = "missing"
            else:
                try:
                    shutil.rmtree(workspace_path)
                    cleanup["status"] = "removed"
                except OSError as error:
                    cleanup.update(status="failed", reason=str(error))
            metadata["ephemeralPromptWorkspaceCleanup"] = cleanup
        if workspace["isolationType"] == "git_worktree":
            cleanup = remove_git_worktree(
                repo_path=self._project_path(workspace["projectId"]),
                worktree_path=Path(workspace["path"]),
                connection=self.connection,
                root=self.root,
                project_id=workspace["projectId"],
                workspace_id=workspace_id,
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
                if delete_branch:
                    branch = str((metadata.get("gitWorktree") or {}).get("branchName") or "")
                    branch_cleanup = delete_git_branch(
                        repo_path=self._project_path(workspace["projectId"]),
                        branch_name=branch,
                        connection=self.connection,
                        root=self.root,
                        project_id=workspace["projectId"],
                        workspace_id=workspace_id,
                    )
                    metadata["gitBranchCleanup"] = branch_cleanup
                    if branch_cleanup["status"] == "deleted":
                        self.connection.execute(
                            """
                            UPDATE git_branches
                            SET status = 'deleted', updated_at = ?
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
