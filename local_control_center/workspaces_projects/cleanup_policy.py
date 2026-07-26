"""Política de limpieza de workspaces runtime huérfanos: plan read-only y aplicación confirmada.

El product loop mantiene UN workspace estable por hilo y no lo archiva al terminar el turno, por lo
que los worktrees de hilos archivados/eliminados se acumulan en el repo real del proyecto y degradan
cada ``git worktree list``/``git branch`` (y con ellos el status git bajo el lock global del API).

``build_cleanup_plan`` clasifica candidatos con criterio fail-closed (solo estados terminales
verificables: hilo archivado/eliminado, workflow run terminado, ruta desaparecida, dueño inexistente)
y reporta los worktrees físicos sin fila activa. ``apply_cleanup`` re-valida cada id seleccionado por
el usuario antes de archivar/remover, jamás toca rutas fuera de ``<root>/.tmp/workspaces`` y cierra
con un único ``git worktree prune`` auditado por policy.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.product_loop.repository import stable_task_suffix
from local_control_center.security_policy.git_command_runner import git_available
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.time import iso_after_seconds, utc_now

from .git_worktrees import _ensure_control_workspace, remove_git_worktree, run_brokered_git
from .repository import ACTIVE_WORKSPACE_STATUSES, WorkspacesRepository, row_to_workspace

# Ventana de gracia: un workspace tocado hace menos de esto nunca es candidato, para no competir
# con runs en vuelo cuyo estado terminal todavía no se persiste.
FRESH_WORKSPACE_GRACE_HOURS = 24.0

# task_id que el product loop deriva del hilo (``stable_task_suffix``): la única evidencia de dueño
# que enlaza un workspace con su hilo.
_PRODUCT_TASK_PATTERN = re.compile(r"^product-(?:loop|owner)-(?P<suffix>[a-z0-9]{1,12})$")

# Estados de workflow_runs que cuentan como terminales aunque ``completed_at`` quede nulo.
_TERMINAL_WORKFLOW_STATUSES = {
    "completed",
    "failed",
    "qa_failed",
    "evidence_ready",
    "runtime_unavailable",
    "cancelled",
}

_CLEANUP_TASK_ID = "workspace-cleanup"


def _workspaces_root(root: Path) -> Path:
    return (root / ".tmp" / "workspaces").resolve(strict=False)


def _is_under(path: Path, ancestor: Path) -> bool:
    return path != ancestor and ancestor in path.parents


def _project_row(connection: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT id, name, path FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise KeyError(f"Project not found: {project_id}")
    return row


def _thread_suffix_index(connection: sqlite3.Connection, project_id: str) -> dict[str, list[dict[str, Any]]]:
    """Indexa TODOS los hilos del proyecto por el sufijo estable que llevan sus task ids."""
    rows = connection.execute(
        "SELECT id, title, status FROM project_threads WHERE project_id = ?", (project_id,)
    ).fetchall()
    index: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        suffix = stable_task_suffix(row["id"], row["id"])
        index.setdefault(suffix, []).append({"id": row["id"], "title": row["title"], "status": row["status"]})
    return index


def _loop_suffixes(connection: sqlite3.Connection, project_id: str) -> set[str]:
    rows = connection.execute("SELECT id FROM product_loops WHERE project_id = ?", (project_id,)).fetchall()
    return {stable_task_suffix(None, row["id"]) for row in rows}


def _workflow_run_terminal(connection: sqlite3.Connection, workflow_run_id: str) -> bool | None:
    """True/False si el run existe (terminal o no); None si el run ya no está en la BD."""
    row = connection.execute(
        "SELECT status, completed_at FROM workflow_runs WHERE id = ?", (workflow_run_id,)
    ).fetchone()
    if not row:
        return None
    return bool(row["completed_at"]) or str(row["status"]) in _TERMINAL_WORKFLOW_STATUSES


def _parse_worktree_listing(output: str) -> list[dict[str, Any]]:
    """Parsea ``git worktree list --porcelain`` a entradas {path, branch, prunable}."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        stripped = line.strip()
        if not stripped:
            if current.get("path"):
                entries.append(current)
            current = {}
            continue
        if stripped.startswith("worktree "):
            current["path"] = stripped.removeprefix("worktree ").strip()
        elif stripped.startswith("branch "):
            current["branch"] = stripped.removeprefix("branch ").removeprefix("refs/heads/")
        elif stripped.startswith("prunable"):
            current["prunable"] = stripped.removeprefix("prunable").strip() or "prunable"
    return entries


def _list_repo_worktrees(
    connection: sqlite3.Connection, *, root: Path, project_id: str, repo_path: Path
) -> list[dict[str, Any]]:
    """Lista los worktrees registrados en el repo del proyecto vía git brokered (policy + rastro)."""
    if not git_available() or not (repo_path / ".git").exists():
        return []
    control_workspace_id = _ensure_control_workspace(
        connection, project_id=project_id, path=repo_path, task_id=f"{_CLEANUP_TASK_ID}-git-control"
    )
    result = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=control_workspace_id,
        workspace_path=repo_path,
        cwd=repo_path,
        args=["worktree", "list", "--porcelain"],
        task_id=f"{_CLEANUP_TASK_ID}.worktree_list",
    )
    if result["returnCode"] != 0:
        return []
    return _parse_worktree_listing(result["stdout"])


def _classify_workspace(
    connection: sqlite3.Connection,
    workspace: dict[str, Any],
    *,
    workspaces_root: Path,
    fresh_cutoff: str,
    thread_index: dict[str, list[dict[str, Any]]],
    loop_suffixes: set[str],
) -> dict[str, Any] | None:
    """Devuelve el candidato con su motivo, o None si el workspace queda protegido (fail-closed)."""
    if str(workspace["updatedAt"]) >= fresh_cutoff:
        return None
    workspace_path = Path(workspace["path"]).resolve(strict=False)
    if not _is_under(workspace_path, workspaces_root):
        # Protege los control workspaces (su path es la raíz del repo real) y cualquier fila rara.
        return None

    reason: str | None = None
    thread: dict[str, Any] | None = None
    path_exists = workspace_path.exists()
    if not path_exists:
        reason = "path_missing"
    else:
        workflow_run_id = workspace.get("workflowRunId")
        match = _PRODUCT_TASK_PATTERN.match(str(workspace["taskId"] or ""))
        if workflow_run_id:
            terminal = _workflow_run_terminal(connection, str(workflow_run_id))
            if terminal is True or terminal is None:
                reason = "workflow_finished"
        elif match:
            suffix = match.group("suffix")
            owners = thread_index.get(suffix, [])
            if owners:
                if all(owner["status"] in {"archived", "deleted"} for owner in owners):
                    thread = owners[0]
                    reason = "thread_deleted" if thread["status"] == "deleted" else "thread_archived"
            elif suffix not in loop_suffixes:
                reason = "owner_missing"
    if reason is None:
        return None

    metadata = workspace.get("metadata") or {}
    branch = str(((metadata.get("gitWorktree") or {}).get("branchName")) or "") or None
    return {
        "workspaceId": workspace["id"],
        "taskId": workspace["taskId"],
        "ownerAgentId": workspace["ownerAgentId"],
        "path": str(workspace_path),
        "pathExists": path_exists,
        "isolationType": workspace["isolationType"],
        "branch": branch,
        "reason": reason,
        "threadId": thread["id"] if thread else None,
        "threadTitle": thread["title"] if thread else None,
        "updatedAt": workspace["updatedAt"],
    }


def _collect_state(connection: sqlite3.Connection, *, root: Path, project_id: str) -> dict[str, Any]:
    """Estado compartido de plan/aplicación: candidatos, huérfanos físicos y contexto del repo."""
    project = _project_row(connection, project_id)
    repo_path = Path(project["path"]).resolve(strict=False)
    workspaces_root = _workspaces_root(root)
    fresh_cutoff = iso_after_seconds(utc_now(), -FRESH_WORKSPACE_GRACE_HOURS * 3600)
    placeholders = ",".join("?" for _ in ACTIVE_WORKSPACE_STATUSES)
    rows = connection.execute(
        f"""
        SELECT * FROM workspaces
        WHERE project_id = ? AND status IN ({placeholders})
        ORDER BY created_at ASC, id ASC
        """,
        (project_id, *sorted(ACTIVE_WORKSPACE_STATUSES)),
    ).fetchall()
    active_workspaces = [row_to_workspace(row) for row in rows]
    thread_index = _thread_suffix_index(connection, project_id)
    loop_suffixes = _loop_suffixes(connection, project_id)

    candidates: list[dict[str, Any]] = []
    for workspace in active_workspaces:
        candidate = _classify_workspace(
            connection,
            workspace,
            workspaces_root=workspaces_root,
            fresh_cutoff=fresh_cutoff,
            thread_index=thread_index,
            loop_suffixes=loop_suffixes,
        )
        if candidate:
            candidates.append(candidate)

    active_paths = {str(Path(workspace["path"]).resolve(strict=False)) for workspace in active_workspaces}
    orphans: list[dict[str, Any]] = []
    for entry in _list_repo_worktrees(connection, root=root, project_id=project_id, repo_path=repo_path):
        entry_path = Path(str(entry["path"])).resolve(strict=False)
        if not _is_under(entry_path, workspaces_root):
            continue
        if str(entry_path) in active_paths:
            continue
        orphans.append(
            {
                "path": str(entry_path),
                "branch": entry.get("branch"),
                "directoryExists": entry_path.exists(),
                "prunable": bool(entry.get("prunable")),
            }
        )

    return {
        "project": {"id": project["id"], "name": project["name"], "path": str(repo_path)},
        "repoPath": repo_path,
        "workspacesRoot": workspaces_root,
        "activeCount": len(active_workspaces),
        "candidates": candidates,
        "orphans": orphans,
    }


def build_cleanup_plan(connection: sqlite3.Connection, *, root: Path, project_id: str) -> dict[str, Any]:
    """Plan de limpieza read-only del proyecto: candidatos clasificados + worktrees huérfanos.

    Raises:
        KeyError: si el proyecto no existe.
    """
    state = _collect_state(connection, root=root, project_id=project_id)
    return {
        "projectId": project_id,
        "projectName": state["project"]["name"],
        "generatedAt": utc_now(),
        "candidates": state["candidates"],
        "repoOrphans": state["orphans"],
        "summary": {
            "activeWorkspaceCount": state["activeCount"],
            "candidateCount": len(state["candidates"]),
            "repoOrphanCount": len(state["orphans"]),
        },
    }


def _prune_repo_worktrees(
    connection: sqlite3.Connection, *, root: Path, project_id: str, repo_path: Path
) -> dict[str, Any]:
    if not git_available() or not (repo_path / ".git").exists():
        return {"status": "skipped_not_git_repo"}
    control_workspace_id = _ensure_control_workspace(
        connection, project_id=project_id, path=repo_path, task_id=f"{_CLEANUP_TASK_ID}-git-control"
    )
    result = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=control_workspace_id,
        workspace_path=repo_path,
        cwd=repo_path,
        args=["worktree", "prune"],
        task_id=f"{_CLEANUP_TASK_ID}.prune",
        git_operation="worktree_prune",
    )
    if result["returnCode"] != 0:
        return {"status": "failed", "stderr": result["stderr"].strip()[:1000] or result["reason"]}
    return {"status": "completed"}


def apply_cleanup(
    connection: sqlite3.Connection,
    *,
    root: Path,
    project_id: str,
    workspace_ids: list[str],
    orphan_worktree_paths: list[str],
    delete_branches: bool,
    reason: str,
) -> dict[str, Any]:
    """Aplica la limpieza sobre la selección explícita confirmada por el usuario.

    Cada id se re-clasifica contra el estado actual (fail-closed): lo que dejó de ser candidato se
    reporta como ``skipped_not_candidate`` sin abortar el batch. Los worktrees huérfanos solo se
    remueven si siguen listados en el repo Y viven bajo ``<root>/.tmp/workspaces``. Cierra con un
    único ``git worktree prune`` y registra el evento ``workspace.cleanup.applied``.

    Raises:
        KeyError: si el proyecto no existe.
    """
    state = _collect_state(connection, root=root, project_id=project_id)
    repo_path: Path = state["repoPath"]
    workspaces_root: Path = state["workspacesRoot"]
    candidates_by_id = {item["workspaceId"]: item for item in state["candidates"]}
    orphans_by_path = {item["path"]: item for item in state["orphans"]}
    cleanup_reason = reason.strip() or "Workspace cleanup confirmed by the operator."
    repository = WorkspacesRepository(connection, root=root)

    results: list[dict[str, Any]] = []
    archived_count = 0
    branches_deleted = 0
    seen_ids: set[str] = set()
    for workspace_id in workspace_ids:
        if workspace_id in seen_ids:
            continue
        seen_ids.add(workspace_id)
        candidate = candidates_by_id.get(workspace_id)
        if not candidate:
            results.append(
                {
                    "workspaceId": workspace_id,
                    "status": "skipped_not_candidate",
                    "reason": "Workspace is no longer a cleanup candidate.",
                }
            )
            continue
        try:
            archived = repository.archive_workspace(
                workspace_id, reason=cleanup_reason, delete_branch=delete_branches
            )
        except KeyError:
            results.append(
                {
                    "workspaceId": workspace_id,
                    "status": "skipped_not_candidate",
                    "reason": "Workspace no longer exists.",
                }
            )
            continue
        metadata = archived.get("metadata") or {}
        worktree_cleanup = str((metadata.get("gitWorktreeCleanup") or {}).get("status") or "")
        branch_cleanup = str((metadata.get("gitBranchCleanup") or {}).get("status") or "")
        if branch_cleanup == "deleted":
            branches_deleted += 1
        archived_count += 1
        results.append(
            {
                "workspaceId": workspace_id,
                "status": "archived",
                "reason": candidate["reason"],
                "worktreeCleanup": worktree_cleanup or None,
                "branchCleanup": branch_cleanup or None,
            }
        )

    orphan_results: list[dict[str, Any]] = []
    orphan_removed = 0
    seen_paths: set[str] = set()
    for raw_path in orphan_worktree_paths:
        normalized = str(Path(raw_path).resolve(strict=False))
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        if not _is_under(Path(normalized), workspaces_root):
            orphan_results.append(
                {
                    "path": normalized,
                    "status": "refused_outside_root",
                    "reason": "Path is outside the managed workspaces root.",
                }
            )
            continue
        orphan = orphans_by_path.get(normalized)
        if not orphan:
            orphan_results.append(
                {
                    "path": normalized,
                    "status": "refused_not_listed",
                    "reason": "Path is not an orphan worktree of this project.",
                }
            )
            continue
        if not orphan["directoryExists"]:
            orphan_results.append({"path": normalized, "status": "pruned", "reason": None})
            orphan_removed += 1
            continue
        removal = remove_git_worktree(
            repo_path=repo_path,
            worktree_path=Path(normalized),
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=f"{_CLEANUP_TASK_ID}-orphan",
        )
        if removal["status"] == "removed":
            orphan_removed += 1
            orphan_results.append({"path": normalized, "status": "removed", "reason": None})
        else:
            orphan_results.append(
                {
                    "path": normalized,
                    "status": "cleanup_failed",
                    "reason": str(removal.get("stderr") or removal["status"]),
                }
            )

    prune = _prune_repo_worktrees(connection, root=root, project_id=project_id, repo_path=repo_path)
    summary = {
        "archivedCount": archived_count,
        "skippedCount": len(results) - archived_count,
        "orphanRemovedCount": orphan_removed,
        "orphanRefusedCount": len(orphan_results) - orphan_removed,
        "branchesDeletedCount": branches_deleted,
    }
    EventBus(connection).record_event(
        project_id=project_id,
        event_type="workspace.cleanup.applied",
        payload={"reason": cleanup_reason, "deleteBranches": delete_branches, **summary},
    )
    return {
        "projectId": project_id,
        "results": results,
        "orphanResults": orphan_results,
        "prune": prune,
        "summary": summary,
    }
