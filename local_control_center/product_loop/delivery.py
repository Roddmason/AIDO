"""Aterrizaje del trabajo aprobado del product loop según el modo de integración del proyecto.

Al aprobarse la entrega, la rama de trabajo (con los commits reales de la iteración) aterriza en
la rama base del proyecto según ``project.git.integrationMode``: ``direct_push`` mergea localmente
(y pushea la base si hay remoto), ``manual_pr``/``auto_pr`` publican la rama para el flujo de PR.
Sin remoto configurado, los modos de PR degradan a ``direct_push`` con aviso explícito. Toda
mutación git corre por los primitivos auditados de ``git_worktrees`` (ToolBroker + policy) y el
resultado se devuelve completo como evidencia; un aterrizaje bloqueado nunca lanza, reporta.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.workspaces_projects.git_worktrees import (
    git_remote_exists,
    merge_work_branch_into_base,
    push_branch_to_remote,
)
from local_control_center.workspaces_projects.repository import WorkspacesRepository

PR_MODES = {"auto_pr", "manual_pr"}


class ProductLoopDeliveryService:
    """Aterriza la rama de trabajo del loop en la base del proyecto según la config git."""

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.workspaces = WorkspacesRepository(connection, root=root)

    def _setting(self, key: str, project_id: str, default: Any) -> Any:
        value = resolve_setting_value(connection=self.connection, key=key, project_id=project_id)
        if value is None:
            return default
        if isinstance(default, str):
            return str(value).strip() or default
        return value

    def git_configuration(self, project_id: str) -> dict[str, Any]:
        """Config git efectiva del proyecto (modo, base, remoto, borrado de rama)."""
        return {
            "integrationMode": self._setting("project.git.integrationMode", project_id, "manual_pr"),
            "baseBranch": self._setting("project.git.baseBranch", project_id, "dev"),
            "remoteName": self._setting("project.git.remoteName", project_id, "origin"),
            "autoDeleteBranch": bool(self._setting("project.git.autoDeleteBranch", project_id, True)),
        }

    def land(
        self,
        *,
        project_id: str,
        workspace_id: str,
        loop_id: str,
        title: str = "",
    ) -> dict[str, Any]:
        """Aterriza el trabajo del workspace del loop según el modo de integración del proyecto.

        Devuelve un dict con ``status`` (``landed``/``pending_manual_pr``/``pending_auto_pr``/
        ``landing_blocked``/``skipped_*``) y la evidencia de cada paso (merge/push/archive).
        Nunca lanza: cualquier bloqueo se reporta con su causa para que el operador decida.
        """
        try:
            workspace = self.workspaces.get_workspace(workspace_id)
        except KeyError:
            return {"status": "skipped_workspace_missing", "workspaceId": workspace_id}
        if workspace["isolationType"] != "git_worktree":
            return {"status": "skipped_not_git_worktree", "workspaceId": workspace_id}
        worktree_meta = (workspace.get("metadata") or {}).get("gitWorktree") or {}
        work_branch = str(worktree_meta.get("branchName") or "").strip()
        if not work_branch:
            return {"status": "skipped_branch_unknown", "workspaceId": workspace_id}
        try:
            project = ProjectsRepository(self.connection).get_project(project_id)
        except KeyError:
            return {"status": "skipped_project_missing", "projectId": project_id}
        repo_path = Path(str(project.get("path") or "")).resolve(strict=False)

        configuration = self.git_configuration(project_id)
        mode = str(configuration["integrationMode"])
        base_branch = str(configuration["baseBranch"])
        remote = str(configuration["remoteName"])
        auto_delete = bool(configuration["autoDeleteBranch"])
        has_remote = git_remote_exists(
            repo_path=repo_path,
            remote=remote,
            connection=self.connection,
            root=self.root,
            project_id=project_id,
        )
        effective_mode = mode
        degraded_from: str | None = None
        if mode in PR_MODES and not has_remote:
            # Sin remoto no puede existir un PR: se degrada a merge local con aviso explícito.
            effective_mode = "direct_push"
            degraded_from = mode

        result: dict[str, Any] = {
            "loopId": loop_id,
            "workspaceId": workspace_id,
            "workBranch": work_branch,
            "configuration": configuration,
            "effectiveMode": effective_mode,
            "degradedFrom": degraded_from,
            "hasRemote": has_remote,
        }

        if effective_mode == "direct_push":
            message = f"Merge {work_branch}: {title or loop_id}"
            merged = merge_work_branch_into_base(
                repo_path=repo_path,
                work_branch=work_branch,
                base_branch=base_branch,
                message=message,
                connection=self.connection,
                root=self.root,
                project_id=project_id,
            )
            if merged.get("status") == "base_branch_missing":
                # La base configurada no existe en este repo: aterrizar en la rama de origen real
                # del worktree (la misma de la que se forkeó), si la hay.
                source_branch = str(worktree_meta.get("sourceBranch") or "").strip()
                if source_branch and source_branch != base_branch:
                    base_branch = source_branch
                    merged = merge_work_branch_into_base(
                        repo_path=repo_path,
                        work_branch=work_branch,
                        base_branch=base_branch,
                        message=message,
                        connection=self.connection,
                        root=self.root,
                        project_id=project_id,
                    )
            result["merge"] = merged
            result["baseBranch"] = base_branch
            if merged.get("status") != "merged":
                result["status"] = "landing_blocked"
                return result
            if has_remote:
                result["push"] = push_branch_to_remote(
                    repo_path=repo_path,
                    remote=remote,
                    branch=base_branch,
                    connection=self.connection,
                    root=self.root,
                    project_id=project_id,
                )
            result["archive"] = self.workspaces.archive_workspace(
                workspace_id, reason="Product loop delivery landed.", delete_branch=auto_delete
            )["metadata"].get("gitBranchCleanup") or {"status": "kept"}
            result["status"] = "landed"
            return result

        # Modos de PR: publicar la rama de trabajo; la creación/aprobación del PR corre aparte.
        result["push"] = push_branch_to_remote(
            repo_path=repo_path,
            remote=remote,
            branch=work_branch,
            connection=self.connection,
            root=self.root,
            project_id=project_id,
        )
        result["baseBranch"] = base_branch
        if result["push"].get("status") != "pushed":
            result["status"] = "landing_blocked"
            return result
        result["status"] = "pending_auto_pr" if effective_mode == "auto_pr" else "pending_manual_pr"
        return result
