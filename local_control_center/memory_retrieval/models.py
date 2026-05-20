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


class MemoryItemRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    scope: str
    scope_id: str | None = Field(default=None, alias="scopeId")
    kind: str
    content: str
    source_ref: str = Field(alias="sourceRef")
    version: int
    hash: str
    supersedes_id: str | None = Field(default=None, alias="supersedesId")
    created_by_run_id: str | None = Field(default=None, alias="createdByRunId")
    valid_from: str = Field(alias="validFrom")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class MemoryResponse(BaseModel):
    memory_item: MemoryItemRecord = Field(alias="memoryItem")


class MemoryListResponse(BaseModel):
    memory_items: list[MemoryItemRecord] = Field(alias="memoryItems")


class RetrievalSearchRequest(BaseModel):
    query: str = ""
    limit: int = 5


class RetrievalSearchResultRecord(BaseModel):
    score: float
    memory_item: MemoryItemRecord = Field(alias="memoryItem")


class RetrievalSearchResponse(BaseModel):
    results: list[RetrievalSearchResultRecord]


class RetrievalIndexSummary(BaseModel):
    backend: str
    dimensions: int
    ids: list[str]
    indexed: int


class RetrievalReindexResponse(BaseModel):
    index: RetrievalIndexSummary
