from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EmptyObjectRequest(BaseModel):
    pass


class MemoryCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    scope: str = "project"
    scope_id: str | None = Field(default=None, alias="scopeId")
    kind: str = "note"
    content: str
    source_ref: str = Field(default="api", alias="sourceRef")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryResponse(BaseModel):
    memory_item: dict[str, Any] = Field(alias="memoryItem")


class RetrievalSearchRequest(BaseModel):
    query: str = ""
    limit: int = 5


class RetrievalSearchResponse(BaseModel):
    results: list[dict[str, Any]]


class RetrievalReindexResponse(BaseModel):
    index: dict[str, Any]
