"""Contratos HTTP del gestor de ramas: inventario gitflow, limpieza, prune, rename y worktrees.

Cada respuesta conserva ``status``/``reason`` y el rastro brokered (``toolCalls`` y
``policyDecisionIds``) para que la UI muestre el resultado por rama sin inventar estados.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import GitCommandTraceRecord, GitWorkspaceStatus

BranchGitflowType = Literal[
    "integration", "feature", "bugfix", "release", "hotfix", "support", "aido", "other"
]
BranchMergeState = Literal["merged", "squash_probable", "not_merged", "unknown"]
BranchUpstreamState = Literal["none", "tracking", "gone"]


class BranchCommitRecord(BaseModel):
    """Último commit de la rama (fecha de committer, autor y asunto)."""

    hash: str = ""
    date: str = ""
    author: str = ""
    subject: str = ""


class BranchUpstreamRecord(BaseModel):
    """Upstream configurado de una rama local y su estado de tracking."""

    name: str = ""
    status: BranchUpstreamState = "none"
    ahead: int = 0
    behind: int = 0


class BranchWorkspaceRecord(BaseModel):
    """Workspace AIDO (git worktree aislado) asociado a la rama."""

    workspace_id: str = Field(alias="workspaceId")
    path: str
    status: str
    task_id: str = Field(default="", alias="taskId")


class BranchRecord(BaseModel):
    """Una rama local o remote-tracking con su clasificación gitflow y señales de salud."""

    name: str
    ref: str
    kind: Literal["local", "remote"]
    remote: str | None = None
    short_name: str = Field(alias="shortName")
    type: BranchGitflowType
    current: bool = False
    protected: bool = False
    protected_reason: str | None = Field(default=None, alias="protectedReason")
    last_commit: BranchCommitRecord = Field(alias="lastCommit")
    age_days: int | None = Field(default=None, alias="ageDays")
    ahead: int | None = None
    behind: int | None = None
    merge_state: BranchMergeState = Field(alias="mergeState")
    merge_evidence: str | None = Field(default=None, alias="mergeEvidence")
    upstream: BranchUpstreamRecord = Field(default_factory=BranchUpstreamRecord)
    worktree_path: str | None = Field(default=None, alias="worktreePath")
    aido_workspaces: list[BranchWorkspaceRecord] = Field(default_factory=list, alias="aidoWorkspaces")
    stale: bool = False
    far_behind: bool = Field(default=False, alias="farBehind")
    based_on_main: bool = Field(default=False, alias="basedOnMain")
    suggested_name: str | None = Field(default=None, alias="suggestedName")
    deletable: bool = False
    requires_force: bool = Field(default=False, alias="requiresForce")


class BranchHealthIssue(BaseModel):
    """Hallazgo de salud gitflow con las ramas afectadas y la acción sugerida (código)."""

    kind: Literal[
        "merged_not_deleted",
        "squash_probable",
        "non_gitflow",
        "stale",
        "far_behind",
        "based_on_main",
        "upstream_gone",
        "merged_worktree",
        "integration_missing",
    ]
    severity: Literal["info", "warning"]
    branches: list[str] = Field(default_factory=list)
    suggested_action: Literal[
        "delete_merged",
        "review_force_delete",
        "rename",
        "review",
        "update_from_integration",
        "recreate_from_integration",
        "prune",
        "remove_worktree",
        "configure_integration",
    ] = Field(alias="suggestedAction")


class BranchHealthSummary(BaseModel):
    """Resumen de salud gitflow del repositorio."""

    total: int = 0
    local: int = 0
    remote: int = 0
    protected: int = 0
    merged: int = 0
    squash_probable: int = Field(default=0, alias="squashProbable")
    stale: int = 0
    non_gitflow: int = Field(default=0, alias="nonGitflow")
    far_behind: int = Field(default=0, alias="farBehind")
    based_on_main: int = Field(default=0, alias="basedOnMain")
    upstream_gone: int = Field(default=0, alias="upstreamGone")
    issues: list[BranchHealthIssue] = Field(default_factory=list)


class BranchInventoryResponse(BaseModel):
    """Inventario completo de ramas para el gestor (snapshot durable del último scan)."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(default="", alias="workspaceId")
    root: str = ""
    snapshot_at: str | None = Field(default=None, alias="snapshotAt")
    refresh_required: bool = Field(default=True, alias="refreshRequired")
    current_branch: str = Field(default="", alias="currentBranch")
    integration_branch: str = Field(default="dev", alias="integrationBranch")
    integration_exists: bool = Field(default=False, alias="integrationExists")
    mainline_branch: str | None = Field(default=None, alias="mainlineBranch")
    remotes: list[str] = Field(default_factory=list)
    stale_days: int = Field(default=30, alias="staleDays")
    far_behind_commits: int = Field(default=50, alias="farBehindCommits")
    squash_detection: Literal["cherry+merge-tree", "cherry"] = Field(
        default="cherry", alias="squashDetection"
    )
    branches: list[BranchRecord] = Field(default_factory=list)
    health: BranchHealthSummary = Field(default_factory=BranchHealthSummary)
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class BranchScanRequest(BaseModel):
    """Parámetros del scan: umbral de antigüedad y de atraso contra la integración."""

    stale_days: int = Field(default=30, ge=1, le=3650, alias="staleDays")
    far_behind_commits: int = Field(default=50, ge=1, le=100000, alias="farBehindCommits")


class BranchDeleteRequest(BaseModel):
    """Selección explícita a borrar; force y borrado remoto son opt-in por separado."""

    branches: list[str] = Field(default_factory=list, max_length=500)
    force_branches: list[str] = Field(default_factory=list, alias="forceBranches", max_length=500)
    delete_remote: bool = Field(default=False, alias="deleteRemote")
    remote_branches: list[str] = Field(default_factory=list, alias="remoteBranches", max_length=500)


class BranchActionResult(BaseModel):
    """Resultado por rama de una acción del gestor."""

    branch: str
    kind: Literal["local", "remote", "worktree", "remote_refs"]
    action: str
    status: Literal["deleted", "renamed", "pruned", "removed", "skipped", "failed", "blocked"]
    reason: str = ""
    forced: bool = False
    detail: list[str] = Field(default_factory=list)


class BranchActionSummary(BaseModel):
    """Conteo agregado de resultados."""

    done: int = 0
    skipped: int = 0
    failed: int = 0


class BranchActionResponse(BaseModel):
    """Respuesta común de las mutaciones del gestor de ramas."""

    status: GitWorkspaceStatus
    reason: str
    project_id: str = Field(alias="projectId")
    workspace_id: str = Field(default="", alias="workspaceId")
    integration_branch: str = Field(default="dev", alias="integrationBranch")
    results: list[BranchActionResult] = Field(default_factory=list)
    summary: BranchActionSummary = Field(default_factory=BranchActionSummary)
    tool_calls: list[GitCommandTraceRecord] = Field(default_factory=list, alias="toolCalls")
    policy_decision_ids: list[str] = Field(default_factory=list, alias="policyDecisionIds")


class BranchRenameRequest(BaseModel):
    """Renombra una rama local sin upstream a un prefijo gitflow."""

    branch: str
    new_name: str = Field(alias="newName")


class BranchPruneRequest(BaseModel):
    """Prune de refs remote-tracking obsoletas; sin remote explícito recorre todos."""

    remote: str | None = None


class BranchWorktreeRemoveRequest(BaseModel):
    """Workspaces AIDO a retirar porque su rama ya está integrada."""

    workspace_ids: list[str] = Field(default_factory=list, alias="workspaceIds", max_length=200)
