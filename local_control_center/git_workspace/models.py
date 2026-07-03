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


class GitInitRequest(BaseModel):
    """Inicializa Git con una rama default controlada."""

    default_branch: Literal["main", "dev"] = Field(default="main", alias="defaultBranch")


class GitInitResponse(BaseModel):
    """Resultado de ``git init`` brokered, sin commit automatico."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    default_branch: Literal["main", "dev"] = Field(alias="defaultBranch")
    current_branch: str = Field(default="", alias="currentBranch")
    gitignore_created: bool = Field(alias="gitignoreCreated")
    commit_created: bool = Field(alias="commitCreated")
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitRemoteAddRequest(BaseModel):
    """Agrega un remote Git validado y sin credenciales embebidas."""

    name: str
    url: str


class GitRemoteMetadataRecord(BaseModel):
    """Metadata persistida de un remote, sanitizada y sin secretos."""

    name: str
    url: str
    scheme: str
    host: str
    path: str
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    last_tested_at: str | None = Field(default=None, alias="lastTestedAt")
    last_test_status: str | None = Field(default=None, alias="lastTestStatus")
    last_test_reason: str | None = Field(default=None, alias="lastTestReason")


class GitRemoteMutationResponse(BaseModel):
    """Resultado de agregar un remote por ToolBroker."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    remote: GitRemoteMetadataRecord | None = None
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class GitRemoteTestRequest(BaseModel):
    """Prueba opcional de remote; la red debe habilitarse explicitamente."""

    allow_network: bool = Field(default=False, alias="allowNetwork")


class GitRemoteTestResponse(BaseModel):
    """Resultado de ``git ls-remote`` sobre un remote existente."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    remote_name: str = Field(alias="remoteName")
    tested: bool
    output_preview: str = Field(default="", alias="outputPreview")
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


class GitBranchPolicyApplyRequest(BaseModel):
    """Aplica la politica de ramas para trabajo seguro sobre una base elegida."""

    intent: str
    selected_base: str = Field(default="main", alias="selectedBase")
    branch_name: str | None = Field(default=None, alias="branchName")
    create_branch: bool = Field(default=False, alias="createBranch")


class GitBranchPolicyApplyResponse(BaseModel):
    """Sugerencia/creacion de rama de trabajo con ramas protegidas bloqueadas."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(alias="workspaceId")
    intent: str
    selected_base: str = Field(alias="selectedBase")
    suggested_branch_name: str = Field(alias="suggestedBranchName")
    target_branch: str = Field(alias="targetBranch")
    protected_branches: list[str] = Field(alias="protectedBranches")
    created: bool
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
