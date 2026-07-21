"""Aislamiento por git worktree: crea/elimina ramas-rama de trabajo y captura su diff.

Provee a un proyecto que es repositorio git un workspace independiente sobre una rama nueva,
sin tocar el árbol original, y al archivar captura el diff resultante. En flujos productivos,
Git se ejecuta mediante ToolBroker/policy sobre workspaces asignados; ante git ausente o repo
inválido devuelve estados ``degraded_*`` en vez de lanzar, para que el aislamiento sea opcional.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

GIT_WORKSPACE_AGENT_ID = "git_workspace_agent"
GIT_COMMAND_TIMEOUT_SECONDS = 30


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
    static_error = _branch_name_static_error(branch_name)
    if static_error:
        return static_error
    clean_name = branch_name.strip()
    ref_check = run_git(["-C", str(repo_path), "check-ref-format", "--branch", clean_name])
    if ref_check.returncode != 0:
        return ref_check.stderr.strip() or "Branch name is not accepted by git check-ref-format."
    existing = run_git(["-C", str(repo_path), "show-ref", "--verify", "--quiet", f"refs/heads/{clean_name}"])
    if existing.returncode == 0:
        return f"Branch already exists: {clean_name}"
    return None


def _branch_name_static_error(branch_name: str) -> str | None:
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
    return None


def git_workspace_agent_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    """Upsert and return the dedicated agent profile under which git operations are brokered.

    The profile is dev-safe, shell-only, local CLI (no remote/API), gated to the
    ``git_worktree``/``git_diff`` quality gates so every git command runs through policy.
    """
    return AgentsRepository(connection).upsert_agent_profile(
        {
            "id": GIT_WORKSPACE_AGENT_ID,
            "name": "AIDO Git Workspace Agent",
            "role": "devops_engineer",
            "runtimeMode": "manual",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
            "allowedProviders": [],
            "allowedRuntimes": [],
            "allowRemote": False,
            "allowCli": True,
            "allowApi": False,
            "qualityGates": ["git_worktree", "git_diff"],
            "outputSchema": {},
        }
    )


def _ensure_control_workspace(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    path: Path,
    task_id: str,
) -> str:
    resolved = str(path.resolve(strict=False))
    existing = connection.execute(
        """
        SELECT * FROM workspaces
        WHERE project_id = ? AND path = ? AND owner_agent_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id, resolved, GIT_WORKSPACE_AGENT_ID),
    ).fetchone()
    if existing:
        return str(existing["id"])
    timestamp = utc_now()
    workspace_id = f"workspace-{uuid.uuid4()}"
    connection.execute(
        """
        INSERT INTO workspaces
            (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
             metadata, created_at, updated_at, archived_at, workflow_run_id, workflow_step_id)
        VALUES (?, ?, ?, ?, ?, 'ready', 'directory', ?, ?, ?, NULL, NULL, NULL)
        """,
        (
            workspace_id,
            project_id,
            task_id,
            GIT_WORKSPACE_AGENT_ID,
            resolved,
            json_dumps(
                {
                    "kind": "git_control_workspace",
                    "source": "git_worktrees",
                    "reason": "Policy-gated Git worktree/diff command boundary.",
                }
            ),
            timestamp,
            timestamp,
        ),
    )
    return workspace_id


def _trace_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution = payload.get("executionResult") if isinstance(payload.get("executionResult"), dict) else {}
    return {
        "toolCallId": tool_call["id"],
        "toolCallStatus": tool_call.get("status") or "",
        "permissionDecisionId": payload.get("permissionDecisionId"),
        "execution": payload.get("execution") or "",
        "returnCode": execution.get("returnCode"),
    }


def _command_display(args: list[str]) -> str:
    return "git " + " ".join(args)


def run_brokered_git(
    *,
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
    workspace_id: str,
    workspace_path: Path,
    cwd: Path,
    args: list[str],
    task_id: str,
    git_operation: str | None = None,
) -> dict[str, Any]:
    """Run a single ``git`` invocation through the ToolBroker on an assigned workspace.

    Records an agent run, evaluates the tool call against policy/sandbox, and returns the
    normalized execution result (returnCode/stdout/stderr/trace). Used for all mutating and
    state-reading git commands so they stay inside the workspace boundary and audit trail.
    """
    agents = AgentsRepository(connection)
    profile = git_workspace_agent_profile(connection)
    agent_run = agents.create_agent_run(
        project_id=project_id,
        agent_profile_id=profile["id"],
        task_id=f"git_worktree.{task_id}",
        input_payload={
            "operation": "git_workspace_command",
            "gitOperation": git_operation,
            "workspaceId": workspace_id,
            "argv": ["git", *args],
        },
        output_payload={},
        status="running",
    )
    broker_result = ToolBroker(connection, artifact_root=root).evaluate_tool_call(
        project_id=project_id,
        agent_run_id=agent_run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "shell",
            "command": _command_display(args),
            "argv": ["git", *args],
            "workspaceId": workspace_id,
            "workspacePath": str(workspace_path),
            "path": str(cwd),
            "operation": "git_workspace_command",
            "runtimeId": "git",
            "gitOperation": git_operation,
            "capability": "git",
            "networkRequired": False,
            "secretsRequired": False,
            "execute": True,
            "timeoutSeconds": GIT_COMMAND_TIMEOUT_SECONDS,
        },
    )
    tool_call = broker_result["toolCall"]
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult")
    if not isinstance(execution_result, dict):
        execution_result = {
            "returnCode": None,
            "stdout": "",
            "stderr": "",
            "blocked": True,
            "reason": payload.get("decisionReason") or "Command was not executed by ToolBroker.",
        }
    return_code = execution_result.get("returnCode")
    status = "blocked" if execution_result.get("blocked") else "completed" if return_code == 0 else "failed"
    trace = {**_trace_from_tool_call(tool_call), "agentRunId": agent_run["id"]}
    output = {"status": status, "trace": trace, "executionResult": execution_result}
    agents.update_agent_run_status(
        agent_run["id"],
        status="completed" if status == "completed" else "failed",
        output_payload=output,
    )
    return {
        "returnCode": return_code if isinstance(return_code, int) else None,
        "stdout": str(execution_result.get("stdout") or ""),
        "stderr": str(execution_result.get("stderr") or ""),
        "status": status,
        "reason": str(execution_result.get("reason") or payload.get("decisionReason") or ""),
        "trace": trace,
        "agentRunId": agent_run["id"],
    }


def _policy_ids(traces: list[dict[str, Any]]) -> list[str]:
    return [str(trace["permissionDecisionId"]) for trace in traces if trace.get("permissionDecisionId")]


def create_git_worktree(
    *,
    repo_path: Path,
    worktree_path: Path,
    task_id: str,
    workspace_id: str,
    base_branch: str = "HEAD",
    branch_name: str | None = None,
    connection: sqlite3.Connection | None = None,
    root: Path | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Crea un worktree sobre una rama nueva derivada de ``base_branch`` para el workspace.

    Si no se da ``branch_name`` deriva uno determinista desde la tarea y el workspace. Nunca
    lanza: ante git ausente, repo inválido, nombre de rama inseguro o fallo de ``worktree add``
    devuelve un dict con ``status`` ``degraded_*`` y el detalle, dejando que el caller decida.
    """
    if not git_available():
        return {"status": "degraded_git_unavailable"}
    resolved_branch_name = (
        branch_name or f"aido/{slugify_branch_segment(task_id)}/{workspace_id.removeprefix('workspace-')[:8]}"
    )
    branch_error = _branch_name_static_error(resolved_branch_name)
    if branch_error:
        return {
            "status": "degraded_invalid_branch_name",
            "branchName": resolved_branch_name,
            "stderr": branch_error,
        }
    if connection is not None and root is not None and project_id:
        control_workspace_id = _ensure_control_workspace(
            connection,
            project_id=project_id,
            path=repo_path,
            task_id=f"{task_id}-git-control",
        )
        traces: list[dict[str, Any]] = []
        repo_check = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=["rev-parse", "--is-inside-work-tree"],
            task_id=f"{task_id}.repo_check",
        )
        traces.append(repo_check["trace"])
        if repo_check["returnCode"] != 0 or repo_check["stdout"].strip() != "true":
            return {
                "status": "degraded_not_git_repo",
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        source_commit_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=["rev-parse", base_branch],
            task_id=f"{task_id}.source_commit",
        )
        source_branch_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=["branch", "--show-current"],
            task_id=f"{task_id}.source_branch",
        )
        traces.extend([source_commit_result["trace"], source_branch_result["trace"]])
        source_commit = (
            source_commit_result["stdout"].strip() if source_commit_result["returnCode"] == 0 else None
        )
        source_branch = (
            source_branch_result["stdout"].strip() if source_branch_result["returnCode"] == 0 else None
        )
        if base_branch != "HEAD" and source_commit is None:
            # La rama base configurada (p. ej. project.git.baseBranch=dev) no existe en este repo;
            # se forkea de HEAD para no degradar el aislamiento git a copia de fuente.
            base_branch = "HEAD"
            head_commit_result = run_brokered_git(
                connection=connection,
                root=root,
                project_id=project_id,
                workspace_id=control_workspace_id,
                workspace_path=repo_path,
                cwd=repo_path,
                args=["rev-parse", "HEAD"],
                task_id=f"{task_id}.head_commit",
            )
            traces.append(head_commit_result["trace"])
            source_commit = (
                head_commit_result["stdout"].strip() if head_commit_result["returnCode"] == 0 else None
            )
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=["worktree", "add", "-b", resolved_branch_name, str(worktree_path), base_branch],
            task_id=f"{task_id}.worktree_add",
            git_operation="worktree_add",
        )
        traces.append(result["trace"])
        if result["returnCode"] != 0:
            return {
                "status": "degraded_worktree_failed",
                "branchName": resolved_branch_name,
                "stderr": result["stderr"].strip()[:1000] or result["reason"],
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        return {
            "status": "created",
            "branchName": resolved_branch_name,
            "baseBranch": base_branch,
            "sourceCommit": source_commit,
            "sourceBranch": source_branch,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }

    if not is_git_repository(repo_path):
        return {"status": "degraded_not_git_repo"}
    source_commit = git_head_commit(repo_path, base_branch)
    if base_branch != "HEAD" and source_commit is None:
        # La rama base configurada no existe en este repo; forkear de HEAD (ver path brokered).
        base_branch = "HEAD"
        source_commit = git_head_commit(repo_path, "HEAD")
    source_branch = git_current_branch(repo_path)
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


def remove_git_worktree(
    *,
    repo_path: Path,
    worktree_path: Path,
    connection: sqlite3.Connection | None = None,
    root: Path | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Elimina forzadamente el worktree del workspace; devuelve ``removed`` o el motivo del fallo."""
    if not git_available():
        return {"status": "cleanup_failed_git_unavailable"}
    if connection is not None and root is not None and project_id and workspace_id:
        control_workspace_id = _ensure_control_workspace(
            connection,
            project_id=project_id,
            path=repo_path,
            task_id="worktree-remove-git-control",
        )
        traces: list[dict[str, Any]] = []
        result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=["worktree", "remove", "--force", str(worktree_path)],
            task_id="worktree_remove",
            git_operation="worktree_remove",
        )
        traces.append(result["trace"])
        if result["returnCode"] != 0:
            return {
                "status": "cleanup_failed",
                "stderr": result["stderr"].strip()[:1000] or result["reason"],
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        return {"status": "removed", "toolCalls": traces, "policyDecisionIds": _policy_ids(traces)}
    result = run_git(["-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)])
    if result.returncode != 0:
        return {"status": "cleanup_failed", "stderr": result.stderr.strip()[:1000]}
    return {"status": "removed"}


def delete_git_branch(
    *,
    repo_path: Path,
    branch_name: str,
    connection: sqlite3.Connection | None = None,
    root: Path | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Borra el ref de una rama de trabajo (``git branch -D``); devuelve ``deleted`` o el motivo.

    Debe correr DESPUÉS de remover el worktree (una rama con worktree activo no se puede borrar).
    Rechaza nombres inseguros/protegidos (HEAD/main/master vía ``_branch_name_static_error``), de
    modo que la rama de integración base nunca puede eliminarse por esta vía.
    """
    if not git_available():
        return {"status": "delete_failed_git_unavailable"}
    if not branch_name or _branch_name_static_error(branch_name):
        return {"status": "delete_skipped_invalid_branch", "branchName": branch_name}
    if connection is not None and root is not None and project_id and workspace_id:
        control_workspace_id = _ensure_control_workspace(
            connection,
            project_id=project_id,
            path=repo_path,
            task_id="worktree-branch-delete-git-control",
        )
        result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=["branch", "-D", branch_name],
            task_id="branch_delete",
            git_operation="delete_branch",
        )
        traces = [result["trace"]]
        if result["returnCode"] != 0:
            return {
                "status": "delete_failed",
                "branchName": branch_name,
                "stderr": result["stderr"].strip()[:1000] or result["reason"],
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        return {
            "status": "deleted",
            "branchName": branch_name,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    result = run_git(["-C", str(repo_path), "branch", "-D", branch_name])
    if result.returncode != 0:
        return {"status": "delete_failed", "branchName": branch_name, "stderr": result.stderr.strip()[:1000]}
    return {"status": "deleted", "branchName": branch_name}


def git_remote_exists(
    *,
    repo_path: Path,
    remote: str,
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
) -> bool:
    """Indica si el repo tiene configurado el remoto dado (vía ``git remote -v`` auditado)."""
    if not git_available() or not remote:
        return False
    control_workspace_id = _ensure_control_workspace(
        connection, project_id=project_id, path=repo_path, task_id="landing-remote-check-git-control"
    )
    result = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=control_workspace_id,
        workspace_path=repo_path,
        cwd=repo_path,
        args=["remote", "-v"],
        task_id="landing_remote_check",
    )
    if result["returnCode"] != 0:
        return False
    return any(line.split("\t")[0] == remote for line in result["stdout"].splitlines() if "\t" in line)


def push_branch_to_remote(
    *,
    repo_path: Path,
    remote: str,
    branch: str,
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
) -> dict[str, Any]:
    """Pushea una rama al remoto configurado (``git push <remote> <branch>``, sin force ni refspec).

    Es la pieza de red del aterrizaje: publica la rama base tras un merge local (``direct_push``)
    o la rama de trabajo antes de abrir un PR. Corre por el ToolBroker con ``networkRequired``
    explícito; la policy deniega cualquier flag de force o refspec.
    """
    if not git_available():
        return {"status": "push_failed_git_unavailable"}
    control_workspace_id = _ensure_control_workspace(
        connection, project_id=project_id, path=repo_path, task_id="landing-push-git-control"
    )
    agents = AgentsRepository(connection)
    profile = git_workspace_agent_profile(connection)
    agent_run = agents.create_agent_run(
        project_id=project_id,
        agent_profile_id=profile["id"],
        task_id="git_worktree.landing_push",
        input_payload={
            "operation": "git_workspace_command",
            "gitOperation": "push_branch",
            "workspaceId": control_workspace_id,
            "argv": ["git", "push", remote, branch],
        },
        output_payload={},
        status="running",
    )
    broker_result = ToolBroker(connection, artifact_root=root).evaluate_tool_call(
        project_id=project_id,
        agent_run_id=agent_run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "shell",
            "command": f"git push {remote} {branch}",
            "argv": ["git", "push", remote, branch],
            "workspaceId": control_workspace_id,
            "workspacePath": str(repo_path),
            "path": str(repo_path),
            "operation": "git_workspace_command",
            "runtimeId": "git",
            "gitOperation": "push_branch",
            "capability": "git",
            "networkRequired": True,
            "secretsRequired": False,
            "execute": True,
            "timeoutSeconds": GIT_COMMAND_TIMEOUT_SECONDS,
        },
    )
    tool_call = broker_result["toolCall"]
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    return_code = execution_result.get("returnCode")
    status = "blocked" if execution_result.get("blocked") else "pushed" if return_code == 0 else "push_failed"
    trace = {**_trace_from_tool_call(tool_call), "agentRunId": agent_run["id"]}
    agents.update_agent_run_status(
        agent_run["id"],
        status="completed" if status == "pushed" else "failed",
        output_payload={"status": status, "trace": trace},
    )
    return {
        "status": status,
        "remote": remote,
        "branch": branch,
        "stderr": str(execution_result.get("stderr") or "").strip()[:1000],
        "toolCalls": [trace],
        "policyDecisionIds": _policy_ids([trace]),
    }


def merge_work_branch_into_base(
    *,
    repo_path: Path,
    work_branch: str,
    base_branch: str,
    message: str,
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
) -> dict[str, Any]:
    """Mergea la rama de trabajo en la rama base del proyecto sin romper el árbol principal.

    Dos caminos, ambos auditados por policy:
    - Si el repo está parado EN la base y limpio, mergea ahí (``merge --no-ff``); ante conflicto
      hace ``merge --abort`` y reporta ``merge_conflict`` sin dejar el árbol a medias.
    - Si la base no está checked-out, abre un worktree temporal con una rama de aterrizaje desde
      la base, mergea allí, verifica que el avance sea append-only (el primer padre del merge es
      la punta actual de la base) y recién entonces mueve el ref (``branch -f base landing``).
    Nunca toca la base si el working tree está sucio o el merge no aplica limpio.
    """
    if not git_available():
        return {"status": "merge_failed_git_unavailable"}
    traces: list[dict[str, Any]] = []
    control_workspace_id = _ensure_control_workspace(
        connection, project_id=project_id, path=repo_path, task_id="landing-merge-git-control"
    )

    def _repo_git(args: list[str], *, git_operation: str | None = None, task: str) -> dict[str, Any]:
        result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=control_workspace_id,
            workspace_path=repo_path,
            cwd=repo_path,
            args=args,
            task_id=task,
            git_operation=git_operation,
        )
        traces.append(result["trace"])
        return result

    base_tip = _repo_git(["rev-parse", base_branch], task="landing_base_tip")
    if base_tip["returnCode"] != 0:
        return {
            "status": "base_branch_missing",
            "baseBranch": base_branch,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    current = _repo_git(["branch", "--show-current"], task="landing_current_branch")
    current_branch = current["stdout"].strip() if current["returnCode"] == 0 else ""

    if current_branch == base_branch:
        pending = _repo_git(["status", "--porcelain"], task="landing_base_status")
        if pending["returnCode"] != 0 or pending["stdout"].strip():
            return {
                "status": "base_dirty",
                "baseBranch": base_branch,
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        merged = _repo_git(
            ["merge", "--no-ff", "-m", message, work_branch],
            git_operation="merge_work_branch",
            task="landing_merge",
        )
        if merged["returnCode"] != 0:
            _repo_git(["merge", "--abort"], git_operation="merge_work_branch", task="landing_merge_abort")
            return {
                "status": "merge_conflict",
                "stderr": merged["stderr"].strip()[:1000] or merged["reason"],
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        head = _repo_git(["rev-parse", "HEAD"], task="landing_base_head")
        return {
            "status": "merged",
            "baseBranch": base_branch,
            "baseCommit": head["stdout"].strip() if head["returnCode"] == 0 else None,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }

    landing_suffix = uuid.uuid4().hex[:8]
    landing_branch = f"aido/landing/{landing_suffix}"
    landing_path = root / ".tmp" / "workspaces" / f"landing-{landing_suffix}"
    created = create_git_worktree(
        repo_path=repo_path,
        worktree_path=landing_path,
        task_id=f"landing-{landing_suffix}",
        workspace_id=f"landing-{landing_suffix}",
        base_branch=base_branch,
        branch_name=landing_branch,
        connection=connection,
        root=root,
        project_id=project_id,
    )
    if created.get("status") != "created":
        return {
            "status": "landing_worktree_failed",
            "detail": created,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    landing_workspace_id = _ensure_control_workspace(
        connection, project_id=project_id, path=landing_path, task_id=f"landing-merge-{landing_suffix}"
    )

    def _landing_git(args: list[str], *, git_operation: str | None = None, task: str) -> dict[str, Any]:
        result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=landing_workspace_id,
            workspace_path=landing_path,
            cwd=landing_path,
            args=args,
            task_id=task,
            git_operation=git_operation,
        )
        traces.append(result["trace"])
        return result

    def _cleanup_landing() -> None:
        remove_git_worktree(
            repo_path=repo_path,
            worktree_path=landing_path,
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=landing_workspace_id,
        )
        delete_git_branch(
            repo_path=repo_path,
            branch_name=landing_branch,
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=landing_workspace_id,
        )

    _ensure_git_identity(landing_path)
    merged = _landing_git(
        ["merge", "--no-ff", "-m", message, work_branch],
        git_operation="merge_work_branch",
        task="landing_merge",
    )
    if merged["returnCode"] != 0:
        _cleanup_landing()
        return {
            "status": "merge_conflict",
            "stderr": merged["stderr"].strip()[:1000] or merged["reason"],
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    first_parent = _landing_git(["rev-parse", f"{landing_branch}^1"], task="landing_first_parent")
    if first_parent["returnCode"] != 0 or first_parent["stdout"].strip() != base_tip["stdout"].strip():
        # La base se movió mientras aterrizábamos: abortar antes de mover el ref (append-only).
        _cleanup_landing()
        return {
            "status": "base_moved_during_landing",
            "baseBranch": base_branch,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    advanced = _repo_git(
        ["branch", "-f", base_branch, landing_branch],
        git_operation="advance_base_after_merge",
        task="landing_advance_base",
    )
    landing_tip = _landing_git(["rev-parse", landing_branch], task="landing_tip")
    _cleanup_landing()
    if advanced["returnCode"] != 0:
        return {
            "status": "base_advance_failed",
            "stderr": advanced["stderr"].strip()[:1000] or advanced["reason"],
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    return {
        "status": "merged",
        "baseBranch": base_branch,
        "baseCommit": landing_tip["stdout"].strip() if landing_tip["returnCode"] == 0 else None,
        "toolCalls": traces,
        "policyDecisionIds": _policy_ids(traces),
    }


def _ensure_git_identity(worktree_path: Path) -> None:
    """Configura una identidad de autor por defecto en el worktree si el repo no tiene ninguna.

    Un ``git commit`` falla sin ``user.name``/``user.email``. Se setea sólo cuando faltan (no pisa
    la identidad existente del proyecto). Es configuración de infraestructura del aislamiento, por
    eso corre por ``run_git`` directo y no como tool call de agente.
    """
    existing = run_git(["-C", str(worktree_path), "config", "user.email"])
    if existing.returncode == 0 and existing.stdout.strip():
        return
    run_git(["-C", str(worktree_path), "config", "user.name", "AIDO"])
    run_git(["-C", str(worktree_path), "config", "user.email", "aido@local"])


def commit_workspace_changes(
    *,
    workspace_path: Path,
    message: str,
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
    workspace_id: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Commitea los cambios del worktree del workspace a su rama de trabajo (``add -A`` + ``commit``).

    Es como el trabajo del agente deja de ser un patch efímero y pasa a ser un commit real sobre la
    rama de la HU (habilita PR/merge/GC reales). ``git add``/``commit`` van por el ToolBroker con
    su rastro de policy. Devuelve ``committed`` con el sha, ``nothing_to_commit`` si no había cambios
    (sin ``allow_empty``), o el motivo del fallo.
    """
    if not git_available():
        return {"status": "commit_failed_git_unavailable"}
    _ensure_git_identity(workspace_path)
    traces: list[dict[str, Any]] = []
    stage = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        cwd=workspace_path,
        args=["add", "-A"],
        task_id="commit_stage",
        git_operation="stage_changes",
    )
    traces.append(stage["trace"])
    if stage["returnCode"] != 0:
        return {
            "status": "commit_failed",
            "stderr": stage["stderr"].strip()[:1000] or stage["reason"],
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    if not allow_empty:
        pending = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["status", "--porcelain"],
            task_id="commit_status",
        )
        traces.append(pending["trace"])
        if pending["returnCode"] == 0 and not pending["stdout"].strip():
            return {
                "status": "nothing_to_commit",
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
    commit_args = ["commit", "-m", message] if not allow_empty else ["commit", "--allow-empty", "-m", message]
    commit = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        cwd=workspace_path,
        args=commit_args,
        task_id="commit_create",
        git_operation="commit_changes",
    )
    traces.append(commit["trace"])
    if commit["returnCode"] != 0:
        return {
            "status": "commit_failed",
            "stderr": commit["stderr"].strip()[:1000] or commit["reason"],
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    head = run_brokered_git(
        connection=connection,
        root=root,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        cwd=workspace_path,
        args=["rev-parse", "HEAD"],
        task_id="commit_head",
    )
    traces.append(head["trace"])
    return {
        "status": "committed",
        "commit": head["stdout"].strip() if head["returnCode"] == 0 else None,
        "toolCalls": traces,
        "policyDecisionIds": _policy_ids(traces),
    }


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


def capture_git_diff(
    workspace_path: Path,
    *,
    connection: sqlite3.Connection | None = None,
    root: Path | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    task_id: str = "git_diff",
) -> dict[str, Any]:
    """Captura el cambio del workspace (status porcelain, name-only, stat y patch) como evidencia.

    Marca archivos nuevos con ``--intent-to-add`` para que aparezcan en el diff. El patch se
    expone íntegro en ``patchFull`` y recortado en ``patch`` (con ``truncated``). Ante git/repo
    no disponible devuelve un estado ``degraded_*`` en lugar de lanzar.
    """
    if not git_available():
        return {"kind": "git_diff", "state": "degraded_git_unavailable", "statusRaw": "", "status": []}
    if connection is not None and root is not None and project_id and workspace_id:
        traces: list[dict[str, Any]] = []
        repo_check = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["rev-parse", "--is-inside-work-tree"],
            task_id=f"{task_id}.repo_check",
        )
        traces.append(repo_check["trace"])
        if repo_check["returnCode"] != 0 or repo_check["stdout"].strip() != "true":
            return {
                "kind": "git_diff",
                "state": "degraded_not_git_repo",
                "statusRaw": "",
                "status": [],
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        status_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["status", "--porcelain=v1"],
            task_id=f"{task_id}.status",
        )
        add_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["add", "--intent-to-add", "--", "."],
            task_id=f"{task_id}.intent_to_add",
            git_operation="diff_capture_intent_to_add",
        )
        branch_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["branch", "--show-current"],
            task_id=f"{task_id}.branch",
        )
        head_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["rev-parse", "HEAD"],
            task_id=f"{task_id}.head",
        )
        name_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["diff", "--name-only"],
            task_id=f"{task_id}.name_only",
        )
        stat_result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=["diff", "--stat", "--", "."],
            task_id=f"{task_id}.stat",
        )
        # git diff captura evidencia inmutable de solo lectura: la salida puede superar el límite
        # de truncación del sandbox (MAX_CAPTURE_CHARS=4000) y el broker no devuelve el contenido
        # completo cuando promote_execution_result_outputs lo promueve a artefacto. Se usa
        # run_git directamente para preservar el diff íntegro (condición de promote_large_git_patches).
        patch_direct = run_git(["-C", str(workspace_path), "diff", "--", "."])
        patch_returncode = patch_direct.returncode
        patch_stdout = patch_direct.stdout if patch_returncode == 0 else ""
        command_results = [
            status_result,
            add_result,
            branch_result,
            head_result,
            name_result,
            stat_result,
        ]
        traces.extend(result["trace"] for result in command_results)
        if status_result["returnCode"] != 0:
            return {
                "kind": "git_diff",
                "state": "capture_failed",
                "stderr": status_result["stderr"].strip()[:2000] or status_result["reason"],
                "files": [],
                "toolCalls": traces,
                "policyDecisionIds": _policy_ids(traces),
            }
        changed = _parse_porcelain_status(status_result["stdout"])
        name_only = [line.strip() for line in name_result["stdout"].splitlines() if line.strip()]
        for item in changed:
            if item["path"] not in name_only:
                name_only.append(item["path"])
        patch = patch_stdout
        return {
            "kind": "git_diff",
            "state": "captured",
            "branch": branch_result["stdout"].strip() if branch_result["returnCode"] == 0 else None,
            "headCommit": head_result["stdout"].strip() if head_result["returnCode"] == 0 else None,
            "statusRaw": status_result["stdout"],
            "status": changed,
            "nameOnly": name_only,
            "diffStat": stat_result["stdout"][:4000] if stat_result["returnCode"] == 0 else "",
            "patch": patch[:12000],
            "patchFull": patch,
            "patchSizeBytes": len(patch.encode("utf-8")),
            "truncated": len(patch) > 12000,
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
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
