"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from local_control_center.evidence.models import EvidencePackageRecord


WorkspaceIsolationType = Literal["directory", "git_worktree"]


class DevcontainerMetadata(BaseModel):
    enabled: bool = False
    template_id: str = Field(default="", alias="templateId")
    image: str = ""
    features: list[str] = Field(default_factory=list)


class WorkspaceAllocateRequest(BaseModel):
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
        return value.lower() if isinstance(value, str) else value


class WorkspaceArchiveRequest(BaseModel):
    reason: str = ""


class WorkspaceRecord(BaseModel):
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
    workspace: WorkspaceRecord


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceRecord]


class WorkspaceArchiveResponse(BaseModel):
    workspace: WorkspaceRecord
    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")
