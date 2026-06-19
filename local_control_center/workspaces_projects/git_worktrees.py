"""Aislamiento por git worktree: crea/elimina ramas-rama de trabajo y captura su diff.

Provee a un proyecto que es repositorio git un workspace independiente sobre una rama nueva,
sin tocar el árbol original, y al archivar captura el diff resultante. Toda invocación a git
pasa por el runner saneado (``security_policy.git_command_runner``); ante git ausente o repo
inválido devuelve estados ``degraded_*`` en vez de lanzar, para que el aislamiento sea opcional.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from local_control_center.security_policy.git_command_runner import git_available, run_git


def slugify_branch_segment(value: str) -> str:
    """Convierte un texto libre en un segmento de rama seguro (<=48 chars), con fallback ``task``."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return cleaned[:48] or "task"


def is_git_repository(path: Path) -> bool:
    """Indica si la ruta está dentro de un árbol de trabajo git (False si git no está disponible)."""
    if not git_available():
        return False
    result = run_git(["-C", str(path), "rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_head_commit(path: Path, ref: str = "HEAD") -> str | None:
    """Resuelve el SHA del ref indicado, o None si git/repo no aplica o el ref no existe."""
    if not git_available() or not is_git_repository(path):
        return None
    result = run_git(["-C", str(path), "rev-parse", ref])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_current_branch(path: Path) -> str | None:
    """Devuelve la rama actual del repo, o None si está en detached HEAD o git/repo no aplica."""
    if not git_available() or not is_git_repository(path):
        return None
    result = run_git(["-C", str(path), "branch", "--show-current"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_branch_name_error(repo_path: Path, branch_name: str) -> str | None:
    """Valida un nombre de rama y devuelve el motivo de rechazo, o None si es aceptable.

    Invariante de seguridad: rechaza refs protegidas (HEAD/main/master), inyección de rutas
    (``..``, barra invertida, segmentos vacíos), nombres que git no acepta y ramas ya
    existentes, de modo que el worktree nunca se cree sobre un destino inseguro ni pise una rama real.
    """
    clean_name = branch_name.strip()
    if not clean_name:
        return "Branch name is required."
    lowered = clean_name.lower()
    if lowered in {"head", "main", "master"}:
        return "Promotion branch cannot target HEAD, main, or master."
    if clean_name.startswith("-"):
        return "Branch name cannot start with '-'."
    if "\\" in clean_name or any(part in {"", ".", ".."} for part in clean_name.split("/")):
        return "Branch name contains an unsafe path segment."
    if clean_name.endswith(("/", ".", ".lock")) or ".." in clean_name or "@{" in clean_name:
        return "Branch name is not a valid Git branch ref."
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", clean_name):
        return "Branch name must contain only letters, numbers, '.', '_', '-' and '/'."

    ref_check = run_git(["-C", str(repo_path), "check-ref-format", "--branch", clean_name])
    if ref_check.returncode != 0:
        return ref_check.stderr.strip() or "Branch name is not accepted by git check-ref-format."
    existing = run_git(["-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/heads/{clean_name}"])
    if existing.returncode == 0:
        return f"Branch already exists: {clean_name}"
    return None


def create_git_worktree(
    *,
    repo_path: Path,
    worktree_path: Path,
    task_id: str,
    workspace_id: str,
    base_branch: str = "HEAD",
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Crea un worktree sobre una rama nueva derivada de ``base_branch`` para el workspace.

    Si no se da ``branch_name`` deriva uno determinista desde la tarea y el workspace. Nunca
    lanza: ante git ausente, repo inválido, nombre de rama inseguro o fallo de ``worktree add``
    devuelve un dict con ``status`` ``degraded_*`` y el detalle, dejando que el caller decida.
    """
    if not git_available():
        return {"status": "degraded_git_unavailable"}
    if not is_git_repository(repo_path):
        return {"status": "degraded_not_git_repo"}
    source_commit = git_head_commit(repo_path, base_branch)
    source_branch = git_current_branch(repo_path)
    resolved_branch_name = (
        branch_name or f"aido/{slugify_branch_segment(task_id)}/{workspace_id.removeprefix('workspace-')[:8]}"
    )
    branch_error = git_branch_name_error(repo_path, resolved_branch_name)
    if branch_error:
        return {
            "status": "degraded_invalid_branch_name",
            "branchName": resolved_branch_name,
            "stderr": branch_error,
        }
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(
        ["-C", str(repo_path), "worktree", "add", "-b", resolved_branch_name, str(worktree_path), base_branch]
    )
    if result.returncode != 0:
        return {
            "status": "degraded_worktree_failed",
            "branchName": resolved_branch_name,
            "stderr": result.stderr.strip()[:1000],
        }
    return {
        "status": "created",
        "branchName": resolved_branch_name,
        "baseBranch": base_branch,
        "sourceCommit": source_commit,
        "sourceBranch": source_branch,
    }


def remove_git_worktree(*, repo_path: Path, worktree_path: Path) -> dict[str, Any]:
    """Elimina forzadamente el worktree del workspace; devuelve ``removed`` o el motivo del fallo."""
    if not git_available():
        return {"status": "cleanup_failed_git_unavailable"}
    result = run_git(["-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)])
    if result.returncode != 0:
        return {"status": "cleanup_failed", "stderr": result.stderr.strip()[:1000]}
    return {"status": "removed"}


def _parse_porcelain_status(output: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2].strip() or line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            previous, current = path.split(" -> ", 1)
            items.append({"status": status, "path": current, "previousPath": previous})
        elif path:
            items.append({"status": status, "path": path})
    return items


def capture_git_diff(workspace_path: Path) -> dict[str, Any]:
    """Captura el cambio del workspace (status porcelain, name-only, stat y patch) como evidencia.

    Marca archivos nuevos con ``--intent-to-add`` para que aparezcan en el diff. El patch se
    expone íntegro en ``patchFull`` y recortado en ``patch`` (con ``truncated``). Ante git/repo
    no disponible devuelve un estado ``degraded_*`` en lugar de lanzar.
    """
    if not git_available():
        return {"kind": "git_diff", "state": "degraded_git_unavailable", "statusRaw": "", "status": []}
    if not is_git_repository(workspace_path):
        return {"kind": "git_diff", "state": "degraded_not_git_repo", "statusRaw": "", "status": []}

    status_result = run_git(["-C", str(workspace_path), "status", "--porcelain=v1"])
    run_git(["-C", str(workspace_path), "add", "--intent-to-add", "--", "."])
    branch_result = run_git(["-C", str(workspace_path), "branch", "--show-current"])
    head_result = run_git(["-C", str(workspace_path), "rev-parse", "HEAD"])
    name_result = run_git(["-C", str(workspace_path), "diff", "--name-only"])
    stat_result = run_git(["-C", str(workspace_path), "diff", "--stat", "--", "."])
    patch_result = run_git(["-C", str(workspace_path), "diff", "--", "."])
    if status_result.returncode != 0:
        return {
            "kind": "git_diff",
            "state": "capture_failed",
            "stderr": status_result.stderr.strip()[:2000],
            "files": [],
        }

    changed = _parse_porcelain_status(status_result.stdout)
    name_only = [line.strip() for line in name_result.stdout.splitlines() if line.strip()]
    for item in changed:
        if item["path"] not in name_only:
            name_only.append(item["path"])

    patch = patch_result.stdout if patch_result.returncode == 0 else ""
    return {
        "kind": "git_diff",
        "state": "captured",
        "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else None,
        "headCommit": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "statusRaw": status_result.stdout,
        "status": changed,
        "nameOnly": name_only,
        "diffStat": stat_result.stdout[:4000] if stat_result.returncode == 0 else "",
        "patch": patch[:12000],
        "patchFull": patch,
        "patchSizeBytes": len(patch.encode("utf-8")),
        "truncated": len(patch) > 12000,
    }
