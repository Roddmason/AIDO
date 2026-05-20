from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PipelineCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    title: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    stages: list[dict[str, Any]] | None = None


class PipelineResponse(BaseModel):
    pipeline: dict[str, Any]


class PipelinesListResponse(BaseModel):
    pipelines: list[dict[str, Any]]
