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
