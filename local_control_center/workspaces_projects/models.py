"""Contratos Pydantic (request/response) del slice de workspaces, con alias camelCase.

Define la forma de los cuerpos de asignación/archivado y de los registros que el API
devuelve al frontend. Los alias mapean snake_case interno a camelCase del JSON público;
no contienen lógica de negocio salvo la normalización del tipo de aislamiento.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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
