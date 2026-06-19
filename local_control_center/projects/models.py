"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from local_control_center.shared.schemas import AuditEventRecord


class ProjectCreateRequest(BaseModel):
    name: str | None = None
    path: str | None = None
    workspace_base_path: str | None = Field(default=None, alias="workspaceBasePath")
    project_directory_name: str | None = Field(default=None, alias="projectDirectoryName")
    template_id: str | None = Field(default=None, alias="templateId")
    create_directory: bool = Field(default=True, alias="createDirectory")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectDiscoveryRequest(BaseModel):
    path: str


class ProjectDiscoveryResponse(BaseModel):
    discovery: dict[str, Any]


class DirectoryPickerRequest(BaseModel):
    title: str = "Select workspace folder"
    initial_path: str | None = Field(default=None, alias="initialPath")


class DirectoryPickerResponse(BaseModel):
    status: str
    selected_path: str | None = Field(default=None, alias="selectedPath")
    reason: str | None = None


class ProjectRecord(BaseModel):
    id: str
    name: str
    path: str
    template_id: str = Field(alias="templateId")
    source: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProjectTemplateRecord(BaseModel):
    id: str
    name: str
    kind: str


class ProviderRecord(BaseModel):
    id: str
    kind: str
    label: str
    capabilities: list[Any]
    models: list[Any]
    status: str
    metadata: dict[str, Any]
    updated_at: str = Field(alias="updatedAt")


class TeamRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    name: str
    version: str
    capabilities: list[Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class CatalogAgentRecord(BaseModel):
    id: str
    team_id: str = Field(alias="teamId")
    name: str
    role: str
    kind: str
    provider_id: str = Field(alias="providerId")
    model: str
    capabilities: list[Any]
    permissions: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ProjectResponse(BaseModel):
    project: ProjectRecord
    audit_event: AuditEventRecord | None = Field(default=None, alias="auditEvent")


class ProjectTemplatesResponse(BaseModel):
    project_templates: list[ProjectTemplateRecord] = Field(alias="projectTemplates")


class ProjectsListResponse(BaseModel):
    projects: list[ProjectRecord]


class ProvidersListResponse(BaseModel):
    providers: list[ProviderRecord]


class TeamsListResponse(BaseModel):
    teams: list[TeamRecord]


class AgentsListResponse(BaseModel):
    agents: list[CatalogAgentRecord]
