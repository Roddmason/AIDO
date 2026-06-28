"""Contratos HTTP del slice Git Workspace: status, ramas, checkout, diff y gitleaks.

Los modelos separan datos Git reales de estados operacionales. Cada respuesta lleva
``status`` y ``reason`` para que la UI pueda mostrar execution/blocked/configuration_required
sin inventar disponibilidad.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GitWorkspaceStatus = Literal["completed", "blocked", "configuration_required", "failed"]


class GitRemoteRecord(BaseModel):
    """Remote Git detectado desde ``git remote -v``."""

    name: str
    url: str
    direction: Literal["fetch", "push"]


class GitCommitRecord(BaseModel):
    """Ultimo commit visible en el repo, si existe."""

    hash: str = ""
    short_hash: str = Field(default="", alias="shortHash")
    author: str = ""
    authored_at: str = Field(default="", alias="authoredAt")
    subject: str = ""


class GitWorktreeRecord(BaseModel):
    """Entrada de ``git worktree list --porcelain``."""

    path: str
    head: str = ""
    branch: str = ""
    detached: bool = False
    bare: bool = False


class GitCommandTraceRecord(BaseModel):
    """Rastro minimo de ejecucion brokered para auditoria de UI/tests."""

    tool_call_id: str = Field(alias="toolCallId")
    tool_call_status: str = Field(alias="toolCallStatus")
    permission_decision_id: str | None = Field(default=None, alias="permissionDecisionId")
    execution: str = ""
    return_code: int | None = Field(default=None, alias="returnCode")


class GitStatusResponse(BaseModel):
    """Snapshot Git completo para la barra de estado y paneles."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    root: str
    current_branch: str = Field(default="", alias="currentBranch")
    dirty: bool = False
    porcelain: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list, alias="changedFiles")
    untracked_files: list[str] = Field(default_factory=list, alias="untrackedFiles")
    staged_files: list[str] = Field(default_factory=list, alias="stagedFiles")
    remotes: list[GitRemoteRecord] = Field(default_factory=list)
    last_commit: GitCommitRecord | None = Field(default=None, alias="lastCommit")
    worktrees: list[GitWorktreeRecord] = Field(default_factory=list)
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitBranchesResponse(BaseModel):
    """Ramas locales/remotas y branch actual."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    current_branch: str = Field(default="", alias="currentBranch")
    dirty: bool = False
    local_branches: list[str] = Field(default_factory=list, alias="localBranches")
    remote_branches: list[str] = Field(default_factory=list, alias="remoteBranches")
    remotes: list[GitRemoteRecord] = Field(default_factory=list)
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitBranchCreateRequest(BaseModel):
    """Crear una rama desde HEAD o desde una base explicita."""

    name: str
    base: str | None = None


class GitBranchMutationResponse(BaseModel):
    """Resultado de crear una rama."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    branch: str
    base: str | None = None
    current_branch: str = Field(default="", alias="currentBranch")
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitCheckoutRequest(BaseModel):
    """Checkout seguro: bloquea dirty tree salvo confirmacion explicita."""

    branch: str
    allow_dirty: bool = Field(default=False, alias="allowDirty")


class GitCheckoutResponse(BaseModel):
    """Resultado de checkout."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    requested_branch: str = Field(alias="requestedBranch")
    current_branch: str = Field(default="", alias="currentBranch")
    dirty: bool = False
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitDiffResponse(BaseModel):
    """Diff Git real contra HEAD."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    root: str
    diff: str
    changed_files: list[str] = Field(default_factory=list, alias="changedFiles")
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitGitleaksRecord(BaseModel):
    """Estado de ejecucion de gitleaks."""

    status: GitWorkspaceStatus
    reason: str
    executable: bool
    configured: bool
    exit_code: int | None = Field(default=None, alias="exitCode")
    finding_count: int = Field(default=0, alias="findingCount")
    report_path: str | None = Field(default=None, alias="reportPath")
    report: Any | None = None
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    permission_decision_id: str | None = Field(default=None, alias="permissionDecisionId")


class GitGitleaksScanResponse(BaseModel):
    """Resultado del gate gitleaks para bloquear entrega si detecta secretos o falla."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    delivery_blocked: bool = Field(alias="deliveryBlocked")
    gitleaks: GitGitleaksRecord
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")

