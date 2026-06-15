"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PipelineCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    title: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    stages: list[dict[str, Any]] | None = None


class PipelineRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    title: str
    status: str
    stages: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PipelineResponse(BaseModel):
    pipeline: PipelineRecord


class PipelinesListResponse(BaseModel):
    pipelines: list[PipelineRecord]
