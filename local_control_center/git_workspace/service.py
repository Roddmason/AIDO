"""Servicio Git Workspace: ejecuta Git/gitleaks local por ToolBroker y policy.

Cada operacion crea un agent run auditable, registra un workspace de proyecto cuando falta
y ejecuta los comandos con argv estructurado bajo ``ToolBroker``. No usa shell ni invoca Git
por fuera del broker; si el runtime local no esta disponible, devuelve estado explicito.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now

GIT_WORKSPACE_AGENT_ID = "git_workspace_agent"
SECURITY_AGENT_ID = "security_agent"
PROJECT_GIT_TASK_ID = "git-workspace"
GITLEAKS_REPORT_DIR = "git-gitleaks-reports"
GIT_TIMEOUT_SECONDS = 30
GITLEAKS_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class GitWorkspaceContext:
    """Contexto resuelto para ejecutar comandos sobre el repo de un proyecto."""

    project: dict[str, Any]
    root: Path
    workspace_id: str


@dataclass(frozen=True)
class OperationContext:
    """Agent run + perfil usado por una operacion brokered."""

    profile: dict[str, Any]
    agent_run: dict[str, Any]


@dataclass(frozen=True)
class BrokeredCommand:
    """Resultado normalizado de un tool call brokered."""

    status: str
    reason: str
    stdout: str
    stderr: str
    return_code: int | None
    trace: dict[str, Any]
    execution_result: dict[str, Any]


def _split_lines(value: str) -> list[str]:
    return [line.rstrip("\r") for line in value.splitlines() if line.rstrip("\r")]


def _command_display(argv: list[str]) -> str:
    if not argv:
        return ""
    return " ".join([Path(argv[0]).name, *argv[1:]])


def _is_safe_ref(value: str | None) -> bool:
    text = (value or "").strip()
    if not text or text != value or len(text) > 160:
        return False
    if text.startswith("-") or text.startswith("/") or text.endswith("/") or text.endswith("."):
        return False
    if ".." in text or "@{" in text or "//" in text:
        return False
    invalid = set(" ~^:?*[\\")
    return not any(char in invalid or ord(char) < 32 for char in text)


def _porcelain_path(raw_path: str) -> str:
    path = raw_path.strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    if len(path) >= 2 and path[0] == path[-1] == '"':
        path = path[1:-1]
    return path


def _parse_porcelain(stdout: str) -> dict[str, list[str]]:
    changed: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in _split_lines(stdout):
        if len(line) < 4:
            continue
        status = line[:2]
        path = _porcelain_path(line[3:])
        if not path:
            continue
        index_status, worktree_status = status[0], status[1]
        if status == "??":
            if path not in untracked:
                untracked.append(path)
            continue
        if index_status not in {" ", "?"} and path not in staged:
            staged.append(path)
        if worktree_status not in {" ", "?"} and path not in changed:
            changed.append(path)
    return {"changedFiles": changed, "untrackedFiles": untracked, "stagedFiles": staged}


def _parse_remotes(stdout: str) -> list[dict[str, str]]:
    remotes: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for line in _split_lines(stdout):
        parts = line.split()
        if len(parts) < 3:
            continue
        direction = parts[2].strip("()")
        if direction not in {"fetch", "push"}:
            continue
        item = (parts[0], parts[1], direction)
        if item in seen:
            continue
        seen.add(item)
        remotes.append({"name": parts[0], "url": parts[1], "direction": direction})
    return remotes


def _parse_last_commit(stdout: str) -> dict[str, str] | None:
    if not stdout.strip():
        return None
    parts = stdout.rstrip("\n").split("\x1f", 4)
    if len(parts) != 5:
        return None
    return {
        "hash": parts[0],
        "shortHash": parts[1],
        "author": parts[2],
        "authoredAt": parts[3],
        "subject": parts[4],
    }


def _short_branch(value: str) -> str:
    prefix = "refs/heads/"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _parse_worktrees(stdout: str) -> list[dict[str, Any]]:
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in [*stdout.splitlines(), ""]:
        line = line.rstrip("\r")
        if not line:
            if current:
                worktrees.append(current)
                current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current:
                worktrees.append(current)
            current = {"path": value, "head": "", "branch": "", "detached": False, "bare": False}
        elif current is not None and key == "HEAD":
            current["head"] = value
        elif current is not None and key == "branch":
            current["branch"] = _short_branch(value)
        elif current is not None and key == "detached":
            current["detached"] = True
        elif current is not None and key == "bare":
            current["bare"] = True
    return worktrees


class GitWorkspaceService:
    """Operaciones Git locales por proyecto bajo ToolBroker."""

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.projects = ProjectsRepository(connection)
        self.agents = AgentsRepository(connection)

    def _project_context(self, project_id: str) -> GitWorkspaceContext:
        project = self.projects.get_project(project_id)
        root = Path(project["path"]).resolve(strict=False)
        if not root.exists() or not root.is_dir():
            return GitWorkspaceContext(project=project, root=root, workspace_id="")
        return GitWorkspaceContext(
            project=project,
            root=root,
            workspace_id=self._ensure_project_workspace(project_id=project_id, root=root),
        )

    def _ensure_project_workspace(self, *, project_id: str, root: Path) -> str:
        existing = self.connection.execute(
            """
            SELECT * FROM workspaces
            WHERE project_id = ? AND path = ? AND owner_agent_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id, str(root), GIT_WORKSPACE_AGENT_ID),
        ).fetchone()
        if existing:
            return str(existing["id"])
        timestamp = utc_now()
        workspace_id = f"workspace-{uuid.uuid4()}"
        metadata = {
            "kind": "project_git_workspace",
            "source": "git_workspace_service",
            "reason": "Project root registered for policy-gated local Git operations.",
        }
        self.connection.execute(
            """
            INSERT INTO workspaces
                (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
                 metadata, created_at, updated_at, archived_at, workflow_run_id, workflow_step_id)
            VALUES (?, ?, ?, ?, ?, 'ready', 'directory', ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                workspace_id,
                project_id,
                PROJECT_GIT_TASK_ID,
                GIT_WORKSPACE_AGENT_ID,
                str(root),
                json_dumps(metadata),
                timestamp,
                timestamp,
            ),
        )
        return workspace_id

    def _git_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
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
                "qualityGates": ["git_status", "git_branch", "git_diff", "gitleaks"],
                "outputSchema": {},
            }
        )

    def _security_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": SECURITY_AGENT_ID,
                "name": "AIDO Security Agent",
                "role": "security_reviewer",
                "runtimeMode": "manual",
                "permissionProfile": "qa",
                "allowedTools": ["shell"],
                "allowedProviders": [],
                "allowedRuntimes": [],
                "allowRemote": False,
                "allowCli": True,
                "allowApi": False,
                "qualityGates": ["gitleaks"],
                "outputSchema": {},
            }
        )

    def _begin_operation(
        self,
        *,
        ctx: GitWorkspaceContext,
        operation: str,
        profile: dict[str, Any],
        payload: dict[str, Any],
    ) -> OperationContext:
        agent_run = self.agents.create_agent_run(
            project_id=ctx.project["id"],
            agent_profile_id=profile["id"],
            task_id=f"git_workspace.{operation}",
            input_payload={"operation": operation, "workspaceId": ctx.workspace_id, **payload},
            output_payload={},
            status="running",
        )
        return OperationContext(profile=profile, agent_run=agent_run)

    def _finish_operation(self, op: OperationContext, *, status: str, output: dict[str, Any]) -> None:
        self.agents.update_agent_run_status(
            op.agent_run["id"],
            status="completed" if status == "completed" else "failed",
            output_payload=output,
        )

    def _trace_from_tool_call(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        payload = tool_call.get("payload") or {}
        execution = payload.get("executionResult") if isinstance(payload.get("executionResult"), dict) else {}
        return {
            "toolCallId": tool_call["id"],
            "toolCallStatus": tool_call.get("status") or "",
            "permissionDecisionId": payload.get("permissionDecisionId"),
            "execution": payload.get("execution") or "",
            "returnCode": execution.get("returnCode"),
        }

    def _run_brokered_command(
        self,
        *,
        ctx: GitWorkspaceContext,
        op: OperationContext,
        argv: list[str],
        operation: str,
        runtime_id: str,
        git_operation: str | None = None,
        capability: str = "git",
        timeout_seconds: int = GIT_TIMEOUT_SECONDS,
    ) -> BrokeredCommand:
        broker_result = ToolBroker(self.connection, artifact_root=self.root).evaluate_tool_call(
            project_id=ctx.project["id"],
            agent_run_id=op.agent_run["id"],
            agent_profile=op.profile,
            tool_call={
                "tool": "shell",
                "command": _command_display(argv),
                "argv": argv,
                "workspaceId": ctx.workspace_id,
                "workspacePath": str(ctx.root),
                "path": str(ctx.root),
                "operation": operation,
                "runtimeId": runtime_id,
                "gitOperation": git_operation,
                "capability": capability,
                "networkRequired": False,
                "secretsRequired": False,
                "execute": True,
                "timeoutSeconds": timeout_seconds,
            },
        )
        tool_call = broker_result["toolCall"]
        payload = tool_call.get("payload") or {}
        execution_result = payload.get("executionResult")
        if not isinstance(execution_result, dict):
            execution_result = {
                "executed": False,
                "blocked": True,
                "reason": payload.get("decisionReason") or "Command was not executed by ToolBroker.",
                "returnCode": None,
                "stdout": "",
                "stderr": "",
            }
        blocked = bool(execution_result.get("blocked"))
        return_code = execution_result.get("returnCode")
        status = "blocked" if blocked else "completed" if return_code == 0 else "failed"
        reason = str(
            execution_result.get("reason")
            or payload.get("decisionReason")
            or ("Command completed." if status == "completed" else "Command failed.")
        )
        return BrokeredCommand(
            status=status,
            reason=reason,
            stdout=str(execution_result.get("stdout") or ""),
            stderr=str(execution_result.get("stderr") or ""),
            return_code=return_code if isinstance(return_code, int) else None,
            trace=self._trace_from_tool_call(tool_call),
            execution_result=execution_result,
        )

    def _run_git(
        self,
        *,
        ctx: GitWorkspaceContext,
        op: OperationContext,
        args: list[str],
        git_operation: str | None = None,
    ) -> BrokeredCommand:
        return self._run_brokered_command(
            ctx=ctx,
            op=op,
            argv=["git", *args],
            operation="git_workspace_command",
            runtime_id="git",
            git_operation=git_operation,
            capability="git",
        )

    def _base_unavailable_response(self, *, ctx: GitWorkspaceContext, reason: str) -> dict[str, Any]:
        return {
            "status": "configuration_required",
            "reason": reason,
            "projectId": ctx.project["id"],
            "workspaceId": ctx.workspace_id,
            "root": str(ctx.root),
            "currentBranch": "",
            "localBranches": [],
            "remoteBranches": [],
            "dirty": False,
            "porcelain": [],
            "changedFiles": [],
            "untrackedFiles": [],
            "stagedFiles": [],
            "remotes": [],
            "lastCommit": None,
            "worktrees": [],
            "toolCalls": [],
            "policyDecisionIds": [],
        }

    def _repo_unavailable(
        self, *, ctx: GitWorkspaceContext, command: BrokeredCommand | None = None
    ) -> dict[str, Any] | None:
        if not ctx.workspace_id:
            return self._base_unavailable_response(
                ctx=ctx, reason="Project path does not exist or is not a directory."
            )
        if command and command.return_code != 0:
            stderr = command.stderr.strip()
            reason = stderr or command.reason
            if "not a git repository" in reason.lower():
                return {
                    **self._base_unavailable_response(
                        ctx=ctx, reason="Project path is not a Git repository."
                    ),
                    "toolCalls": [command.trace],
                    "policyDecisionIds": [command.trace["permissionDecisionId"]]
                    if command.trace.get("permissionDecisionId")
                    else [],
                }
            return {
                **self._base_unavailable_response(ctx=ctx, reason=reason),
                "status": "failed" if command.status == "failed" else "blocked",
                "toolCalls": [command.trace],
                "policyDecisionIds": [command.trace["permissionDecisionId"]]
                if command.trace.get("permissionDecisionId")
                else [],
            }
        return None

    def status(self, project_id: str) -> dict[str, Any]:
        """Devuelve snapshot Git completo del proyecto."""
        ctx = self._project_context(project_id)
        if not shutil.which("git"):
            return self._base_unavailable_response(ctx=ctx, reason="Git executable was not found on PATH.")
        if not ctx.workspace_id:
            return self._base_unavailable_response(
                ctx=ctx, reason="Project path does not exist or is not a directory."
            )
        # The project must own its OWN repository: require a `.git` at the project root. Without this,
        # a project folder nested inside another repo (e.g. inside AIDO's checkout) would make every git
        # command walk UP to the parent repo and report the WRONG project's branches. `.git` is a dir for
        # a normal clone and a file for a worktree/submodule, so `.exists()` accepts both.
        if not (ctx.root / ".git").exists():
            return self._base_unavailable_response(ctx=ctx, reason="Project path is not a Git repository.")
        profile = self._git_profile()
        op = self._begin_operation(ctx=ctx, operation="status", profile=profile, payload={})
        traces: list[dict[str, Any]] = []
        try:
            porcelain_result = self._run_git(ctx=ctx, op=op, args=["status", "--porcelain=v1", "-uall"])
            traces.append(porcelain_result.trace)
            unavailable = self._repo_unavailable(ctx=ctx, command=porcelain_result)
            if unavailable:
                self._finish_operation(op, status=unavailable["status"], output=unavailable)
                return unavailable
            current = self._run_git(ctx=ctx, op=op, args=["branch", "--show-current"])
            local = self._run_git(ctx=ctx, op=op, args=["branch", "--format=%(refname:short)"])
            remote = self._run_git(ctx=ctx, op=op, args=["branch", "--remotes", "--format=%(refname:short)"])
            remotes = self._run_git(ctx=ctx, op=op, args=["remote", "-v"])
            last_commit = self._run_git(
                ctx=ctx,
                op=op,
                args=["log", "-1", "--pretty=format:%H%x1f%h%x1f%an%x1f%aI%x1f%s"],
            )
            worktrees = self._run_git(ctx=ctx, op=op, args=["worktree", "list", "--porcelain"])
            traces.extend(
                [current.trace, local.trace, remote.trace, remotes.trace, last_commit.trace, worktrees.trace]
            )
            parsed = _parse_porcelain(porcelain_result.stdout)
            local_branches = _split_lines(local.stdout) if local.return_code == 0 else []
            remote_branches = _split_lines(remote.stdout) if remote.return_code == 0 else []
            response = {
                "status": "completed",
                "reason": "Git status collected through ToolBroker.",
                "projectId": project_id,
                "workspaceId": ctx.workspace_id,
                "root": str(ctx.root),
                "currentBranch": current.stdout.strip() or "HEAD",
                "localBranches": local_branches,
                "remoteBranches": remote_branches,
                "dirty": bool(parsed["changedFiles"] or parsed["untrackedFiles"] or parsed["stagedFiles"]),
                "porcelain": _split_lines(porcelain_result.stdout),
                "changedFiles": parsed["changedFiles"],
                "untrackedFiles": parsed["untrackedFiles"],
                "stagedFiles": parsed["stagedFiles"],
                "remotes": _parse_remotes(remotes.stdout) if remotes.return_code == 0 else [],
                "lastCommit": _parse_last_commit(last_commit.stdout)
                if last_commit.return_code == 0
                else None,
                "worktrees": _parse_worktrees(worktrees.stdout) if worktrees.return_code == 0 else [],
                "toolCalls": traces,
                "policyDecisionIds": [
                    str(trace["permissionDecisionId"])
                    for trace in traces
                    if trace.get("permissionDecisionId")
                ],
            }
            self._finish_operation(op, status="completed", output=response)
            return response
        except Exception as exc:
            output = self._base_unavailable_response(ctx=ctx, reason=str(exc))
            output["status"] = "failed"
            output["toolCalls"] = traces
            self._finish_operation(op, status="failed", output=output)
            raise

    def branches(self, project_id: str) -> dict[str, Any]:
        """Lista ramas locales/remotas y dirty state reutilizando el snapshot de ``status()``.

        ``status()`` ya ejecuta ``git branch --format`` y ``git branch --remotes`` bajo el broker,
        de modo que esta operacion reformatea ese resultado en vez de volver a lanzar los mismos
        subprocesos: mismo contrato auditado (los comandos siguen brokered en el run de status)
        sin comandos Git redundantes ni un segundo contexto de workspace.
        """
        status = self.status(project_id)
        completed = status["status"] == "completed"
        return {
            "status": status["status"],
            "reason": ("Git branches collected through ToolBroker." if completed else status["reason"]),
            "projectId": project_id,
            "workspaceId": status.get("workspaceId") or "",
            "currentBranch": status.get("currentBranch") or "",
            "dirty": bool(status.get("dirty")),
            "localBranches": list(status.get("localBranches") or []),
            "remoteBranches": list(status.get("remoteBranches") or []),
            "remotes": status.get("remotes") or [],
            "toolCalls": list(status.get("toolCalls") or []),
            "policyDecisionIds": list(status.get("policyDecisionIds") or []),
        }

    def create_branch(self, project_id: str, *, name: str, base: str | None = None) -> dict[str, Any]:
        """Crea una rama local desde HEAD o desde una base explicita validada."""
        ctx = self._project_context(project_id)
        if not _is_safe_ref(name):
            return {
                "status": "blocked",
                "reason": "Branch name is invalid or unsafe for local Git operations.",
                "projectId": project_id,
                "workspaceId": ctx.workspace_id,
                "branch": name,
                "base": base,
                "currentBranch": "",
                "toolCalls": [],
                "policyDecisionIds": [],
            }
        if base is not None and not _is_safe_ref(base):
            return {
                "status": "blocked",
                "reason": "Base ref is invalid or unsafe for local Git operations.",
                "projectId": project_id,
                "workspaceId": ctx.workspace_id,
                "branch": name,
                "base": base,
                "currentBranch": "",
                "toolCalls": [],
                "policyDecisionIds": [],
            }
        profile = self._git_profile()
        op = self._begin_operation(
            ctx=ctx,
            operation="create_branch",
            profile=profile,
            payload={"branch": name, "base": base},
        )
        args = ["branch", name, *([base] if base else [])]
        result = self._run_git(ctx=ctx, op=op, args=args, git_operation="create_branch")
        branch_status = self.branches(project_id)
        traces = [result.trace, *(branch_status.get("toolCalls") or [])]
        response_status = "completed" if result.return_code == 0 else result.status
        reason = (
            "Branch created from the requested base."
            if response_status == "completed"
            else result.stderr.strip() or result.reason
        )
        response = {
            "status": response_status,
            "reason": reason,
            "projectId": project_id,
            "workspaceId": ctx.workspace_id,
            "branch": name,
            "base": base,
            "currentBranch": branch_status.get("currentBranch") or "",
            "toolCalls": traces,
            "policyDecisionIds": [
                str(trace["permissionDecisionId"]) for trace in traces if trace.get("permissionDecisionId")
            ],
        }
        self._finish_operation(op, status=response_status, output=response)
        return response

    def checkout(self, project_id: str, *, branch: str, allow_dirty: bool = False) -> dict[str, Any]:
        """Hace checkout de una rama, bloqueando dirty tree sin confirmacion."""
        ctx = self._project_context(project_id)
        if not _is_safe_ref(branch):
            return {
                "status": "blocked",
                "reason": "Checkout target is invalid or unsafe for local Git operations.",
                "projectId": project_id,
                "workspaceId": ctx.workspace_id,
                "requestedBranch": branch,
                "currentBranch": "",
                "dirty": False,
                "toolCalls": [],
                "policyDecisionIds": [],
            }
        before = self.status(project_id)
        traces = list(before.get("toolCalls") or [])
        if before["status"] != "completed":
            return {
                "status": before["status"],
                "reason": before["reason"],
                "projectId": project_id,
                "workspaceId": before.get("workspaceId") or ctx.workspace_id,
                "requestedBranch": branch,
                "currentBranch": before.get("currentBranch") or "",
                "dirty": bool(before.get("dirty")),
                "toolCalls": traces,
                "policyDecisionIds": before.get("policyDecisionIds") or [],
            }
        if before["dirty"] and not allow_dirty:
            return {
                "status": "blocked",
                "reason": "Dirty tree blocks checkout. Confirm allowDirty=true before switching branches.",
                "projectId": project_id,
                "workspaceId": ctx.workspace_id,
                "requestedBranch": branch,
                "currentBranch": before.get("currentBranch") or "",
                "dirty": True,
                "toolCalls": traces,
                "policyDecisionIds": before.get("policyDecisionIds") or [],
            }
        profile = self._git_profile()
        op = self._begin_operation(
            ctx=ctx,
            operation="checkout",
            profile=profile,
            payload={"branch": branch, "allowDirty": allow_dirty},
        )
        result = self._run_git(ctx=ctx, op=op, args=["checkout", branch], git_operation="checkout")
        after = self.status(project_id)
        traces.extend([result.trace, *(after.get("toolCalls") or [])])
        response_status = "completed" if result.return_code == 0 else result.status
        response = {
            "status": response_status,
            "reason": "Branch checkout completed."
            if response_status == "completed"
            else result.stderr.strip() or result.reason,
            "projectId": project_id,
            "workspaceId": ctx.workspace_id,
            "requestedBranch": branch,
            "currentBranch": after.get("currentBranch") or before.get("currentBranch") or "",
            "dirty": bool(after.get("dirty", before.get("dirty"))),
            "toolCalls": traces,
            "policyDecisionIds": [
                str(trace["permissionDecisionId"]) for trace in traces if trace.get("permissionDecisionId")
            ],
        }
        self._finish_operation(op, status=response_status, output=response)
        return response

    def diff(self, project_id: str) -> dict[str, Any]:
        """Genera diff real contra HEAD y lista archivos cambiados."""
        ctx = self._project_context(project_id)
        profile = self._git_profile()
        op = self._begin_operation(ctx=ctx, operation="diff", profile=profile, payload={})
        status = self.status(project_id)
        traces = list(status.get("toolCalls") or [])
        result = self._run_git(ctx=ctx, op=op, args=["diff", "--no-ext-diff", "HEAD", "--"])
        traces.append(result.trace)
        response_status = "completed" if result.return_code == 0 else result.status
        changed_files = list(
            dict.fromkeys([*(status.get("changedFiles") or []), *(status.get("stagedFiles") or [])])
        )
        response = {
            "status": response_status,
            "reason": "Git diff collected through ToolBroker."
            if response_status == "completed"
            else result.stderr.strip() or result.reason,
            "projectId": project_id,
            "workspaceId": ctx.workspace_id,
            "root": str(ctx.root),
            "diff": result.stdout if result.return_code == 0 else "",
            "changedFiles": changed_files,
            "toolCalls": traces,
            "policyDecisionIds": [
                str(trace["permissionDecisionId"]) for trace in traces if trace.get("permissionDecisionId")
            ],
        }
        self._finish_operation(op, status=response_status, output=response)
        return response

    def _gitleaks_executable(self) -> str | None:
        for candidate in ("gitleaks", "gitleaks.cmd", "gitleaks.exe"):
            executable = shutil.which(candidate)
            if executable:
                return executable
        return None

    def _gitleaks_report_path(self) -> Path:
        report_dir = self.root / ".tmp" / GITLEAKS_REPORT_DIR
        report_dir.mkdir(parents=True, exist_ok=True)
        return report_dir / f"gitleaks-{uuid.uuid4()}.json"

    def _load_report(self, report_path: Path) -> Any:
        if not report_path.exists() or not report_path.is_file():
            return {"parseError": "report_file_missing", "reportPath": str(report_path)}
        raw = report_path.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            return {"parseError": str(exc), "rawPreview": raw[:1000]}

    def gitleaks_scan(
        self,
        project_id: str,
        *,
        workspace_id: str | None = None,
        workspace_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Ejecuta gitleaks por ToolBroker y devuelve gate de entrega."""
        ctx = self._project_context(project_id)
        scan_ctx = ctx
        if workspace_path is not None:
            scan_ctx = GitWorkspaceContext(
                project=ctx.project,
                root=Path(workspace_path).resolve(strict=False),
                workspace_id=workspace_id or ctx.workspace_id,
            )
        executable = self._gitleaks_executable()
        if not executable:
            gitleaks = {
                "status": "configuration_required",
                "reason": "Gitleaks executable was not found on PATH.",
                "executable": False,
                "configured": False,
                "exitCode": None,
                "findingCount": 0,
                "reportPath": None,
                "report": None,
                "toolCallId": None,
                "permissionDecisionId": None,
            }
            return {
                "status": "configuration_required",
                "reason": gitleaks["reason"],
                "projectId": project_id,
                "workspaceId": scan_ctx.workspace_id,
                "deliveryBlocked": True,
                "gitleaks": gitleaks,
                "toolCalls": [],
                "policyDecisionIds": [],
            }
        profile = self._security_profile()
        op = self._begin_operation(ctx=scan_ctx, operation="gitleaks_scan", profile=profile, payload={})
        report_path = self._gitleaks_report_path()
        argv = [
            executable,
            "dir",
            str(scan_ctx.root),
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
        ]
        result = self._run_brokered_command(
            ctx=scan_ctx,
            op=op,
            argv=argv,
            operation="security_agent_scanner",
            runtime_id="gitleaks",
            capability="security_scan",
            timeout_seconds=GITLEAKS_TIMEOUT_SECONDS,
        )
        report = redact_secrets(self._load_report(report_path))
        finding_count = len(report) if isinstance(report, list) else 0
        if result.execution_result.get("timedOut"):
            scanner_status = "failed"
            reason = "Gitleaks execution timed out."
        elif result.status == "blocked":
            scanner_status = "blocked"
            reason = result.reason
        elif result.return_code == 1 or finding_count:
            scanner_status = "blocked"
            reason = f"Gitleaks detected {finding_count} secret finding(s)."
        elif result.return_code == 0:
            scanner_status = "completed"
            reason = "Gitleaks completed with no secret findings."
        else:
            scanner_status = "failed"
            reason = f"Gitleaks exited with code {result.return_code}."
        response_status = "completed" if scanner_status == "completed" else scanner_status
        gitleaks = {
            "status": response_status,
            "reason": reason,
            "executable": result.status != "blocked",
            "configured": True,
            "exitCode": result.return_code,
            "findingCount": finding_count,
            "reportPath": str(report_path),
            "report": report,
            "toolCallId": result.trace["toolCallId"],
            "permissionDecisionId": result.trace.get("permissionDecisionId"),
        }
        response = {
            "status": response_status,
            "reason": reason,
            "projectId": project_id,
            "workspaceId": scan_ctx.workspace_id,
            "deliveryBlocked": response_status != "completed",
            "gitleaks": gitleaks,
            "toolCalls": [result.trace],
            "policyDecisionIds": [result.trace["permissionDecisionId"]]
            if result.trace.get("permissionDecisionId")
            else [],
        }
        self._finish_operation(op, status=response_status, output=response)
        return response
