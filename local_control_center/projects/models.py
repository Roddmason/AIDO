from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    name: str | None = None
    path: str | None = None
    template_id: str | None = Field(default=None, alias="templateId")
    create_directory: bool = Field(default=True, alias="createDirectory")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectResponse(BaseModel):
    project: dict[str, Any]
    audit_event: dict[str, Any] | None = Field(default=None, alias="auditEvent")


class ProjectTemplatesResponse(BaseModel):
    project_templates: list[dict[str, Any]] = Field(alias="projectTemplates")


class ProjectsListResponse(BaseModel):
    projects: list[dict[str, Any]]


class ProvidersListResponse(BaseModel):
    providers: list[dict[str, Any]]


class TeamsListResponse(BaseModel):
    teams: list[dict[str, Any]]


class AgentsListResponse(BaseModel):
    agents: list[dict[str, Any]]
