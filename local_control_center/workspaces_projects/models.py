"""Contratos Pydantic (request/response) del slice de workspaces, con alias camelCase.

Define la forma de los cuerpos de asignación/archivado y de los registros que el API
devuelve al frontend. Los alias mapean snake_case interno a camelCase del JSON público;
no contienen lógica de negocio salvo la normalización del tipo de aislamiento.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from local_control_center.evidence.models import EvidencePackageRecord

WorkspaceIsolationType = Literal["directory", "git_worktree"]


class DevcontainerMetadata(BaseModel):
    """Configuración declarativa de devcontainer adjunta al workspace (persistida, no ejecutada)."""

    enabled: bool = False
    template_id: str = Field(default="", alias="templateId")
    image: str = ""
    features: list[str] = Field(default_factory=list)


class WorkspaceAllocateRequest(BaseModel):
    """Petición para asignar un workspace aislado a una tarea de un proyecto."""

    project_id: str = Field(alias="projectId")
    task_id: str = Field(alias="taskId")
    agent_id: str = Field(alias="agentId")
    reason: str = ""
    isolation_type: WorkspaceIsolationType = Field(default="directory", alias="isolationType")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    base_branch: str = Field(default="HEAD", alias="baseBranch")
    devcontainer: DevcontainerMetadata | None = None

    @field_validator("isolation_type", mode="before")
    @classmethod
    def normalize_isolation_type(cls, value: Any) -> Any:
        """Acepta el tipo de aislamiento en cualquier capitalización normalizándolo a minúsculas."""
        return value.lower() if isinstance(value, str) else value


class WorkspaceArchiveRequest(BaseModel):
    """Petición de archivado; el motivo queda registrado en metadata y en la evidencia."""

    reason: str = ""


class WorkspaceRecord(BaseModel):
    """Estado persistido de un workspace tal como se proyecta al cliente."""

    id: str
    project_id: str = Field(alias="projectId")
    task_id: str = Field(alias="taskId")
    owner_agent_id: str = Field(alias="ownerAgentId")
    path: str
    status: str
    isolation_type: WorkspaceIsolationType = Field(alias="isolationType")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    archived_at: str | None = Field(default=None, alias="archivedAt")


class WorkspaceResponse(BaseModel):
    """Envoltura de un único workspace para los endpoints de creación y consulta."""

    workspace: WorkspaceRecord


class WorkspacesListResponse(BaseModel):
    """Colección de workspaces devuelta por el listado."""

    workspaces: list[WorkspaceRecord]


class WorkspaceArchiveResponse(BaseModel):
    """Resultado del archivado: el workspace ya cerrado más el paquete de evidencia generado."""

    workspace: WorkspaceRecord
    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")


class WorkspaceCleanupCandidate(BaseModel):
    """Workspace runtime activo clasificado como huérfano, con el motivo verificable."""

    workspace_id: str = Field(alias="workspaceId")
    task_id: str = Field(alias="taskId")
    owner_agent_id: str = Field(alias="ownerAgentId")
    path: str
    path_exists: bool = Field(alias="pathExists")
    isolation_type: WorkspaceIsolationType = Field(alias="isolationType")
    branch: str | None = None
    reason: str
    thread_id: str | None = Field(default=None, alias="threadId")
    thread_title: str | None = Field(default=None, alias="threadTitle")
    updated_at: str = Field(alias="updatedAt")


class WorkspaceCleanupOrphan(BaseModel):
    """Worktree físico registrado en el repo del proyecto sin fila de workspace activa."""

    path: str
    branch: str | None = None
    directory_exists: bool = Field(alias="directoryExists")
    prunable: bool = False


class WorkspaceCleanupPlanSummary(BaseModel):
    """Conteos del plan para dimensionar la limpieza antes de confirmar."""

    active_workspace_count: int = Field(alias="activeWorkspaceCount")
    candidate_count: int = Field(alias="candidateCount")
    repo_orphan_count: int = Field(alias="repoOrphanCount")


class WorkspaceCleanupPlanResponse(BaseModel):
    """Plan de limpieza read-only: nada se toca hasta que el usuario confirma una selección."""

    project_id: str = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    generated_at: str = Field(alias="generatedAt")
    candidates: list[WorkspaceCleanupCandidate]
    repo_orphans: list[WorkspaceCleanupOrphan] = Field(alias="repoOrphans")
    summary: WorkspaceCleanupPlanSummary


class WorkspaceCleanupApplyRequest(BaseModel):
    """Selección explícita confirmada por el usuario; no existe un modo 'todo' implícito."""

    workspace_ids: list[str] = Field(default_factory=list, alias="workspaceIds")
    orphan_worktree_paths: list[str] = Field(default_factory=list, alias="orphanWorktreePaths")
    delete_branches: bool = Field(default=False, alias="deleteBranches")
    reason: str = ""

    @model_validator(mode="after")
    def require_explicit_selection(self) -> WorkspaceCleanupApplyRequest:
        """Rechaza la petición vacía: la confirmación es la lista concreta que se revisó."""
        if not self.workspace_ids and not self.orphan_worktree_paths:
            raise ValueError("Select at least one workspace or orphan worktree to clean up.")
        return self


class WorkspaceCleanupItemResult(BaseModel):
    """Resultado por workspace seleccionado; el batch nunca aborta por un ítem."""

    workspace_id: str = Field(alias="workspaceId")
    status: str
    reason: str | None = None
    worktree_cleanup: str | None = Field(default=None, alias="worktreeCleanup")
    branch_cleanup: str | None = Field(default=None, alias="branchCleanup")


class WorkspaceCleanupOrphanResult(BaseModel):
    """Resultado por worktree huérfano seleccionado (removido, pruned o rechazado)."""

    path: str
    status: str
    reason: str | None = None


class WorkspaceCleanupPruneResult(BaseModel):
    """Resultado del ``git worktree prune`` final sobre el repo del proyecto."""

    status: str
    stderr: str | None = None


class WorkspaceCleanupSummary(BaseModel):
    """Conteos agregados de la aplicación, también emitidos en el evento de auditoría."""

    archived_count: int = Field(alias="archivedCount")
    skipped_count: int = Field(alias="skippedCount")
    orphan_removed_count: int = Field(alias="orphanRemovedCount")
    orphan_refused_count: int = Field(alias="orphanRefusedCount")
    branches_deleted_count: int = Field(alias="branchesDeletedCount")


class WorkspaceCleanupApplyResponse(BaseModel):
    """Resultado completo de la limpieza confirmada, ítem por ítem más el prune final."""

    project_id: str = Field(alias="projectId")
    results: list[WorkspaceCleanupItemResult]
    orphan_results: list[WorkspaceCleanupOrphanResult] = Field(alias="orphanResults")
    prune: WorkspaceCleanupPruneResult
    summary: WorkspaceCleanupSummary
