"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    scope: str = "project"
    scope_id: str | None = Field(default=None, alias="scopeId")
    kind: str = "note"
    content: str
    source_ref: str = Field(default="api", alias="sourceRef")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    ttl_seconds: int | None = Field(default=None, alias="ttlSeconds", ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryDeleteRequest(BaseModel):
    reason: str = ""


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
    expires_at: str | None = Field(default=None, alias="expiresAt")
    deleted_at: str | None = Field(default=None, alias="deletedAt")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class MemoryResponse(BaseModel):
    memory_item: MemoryItemRecord = Field(alias="memoryItem")


class MemoryListResponse(BaseModel):
    memory_items: list[MemoryItemRecord] = Field(alias="memoryItems")


class RetrievalSearchRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    query: str = ""
    limit: int = 5


class RetrievalSearchResultRecord(BaseModel):
    score: float
    memory_item: MemoryItemRecord = Field(alias="memoryItem")


class RetrievalSearchResponse(BaseModel):
    status: str
    reason: str
    results: list[RetrievalSearchResultRecord]


class RetrievalIndexSummary(BaseModel):
    status: str
    reason: str
    project_id: str = Field(alias="projectId")
    backend: str
    degraded: bool
    dimensions: int
    ids: list[str]
    indexed: int


class RetrievalReindexRequest(BaseModel):
    project_id: str = Field(alias="projectId")


class RetrievalReindexResponse(BaseModel):
    index: RetrievalIndexSummary
