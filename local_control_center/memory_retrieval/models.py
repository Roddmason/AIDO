"""Esquemas Pydantic del contrato HTTP de memoria y retrieval.

Definen el cuerpo de entrada y la forma de salida que la API expone al
frontend, fijando alias camelCase y valores por defecto del contrato.
Aíslan las claves del transporte de los nombres internos snake_case.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    """Datos para crear un memory item; admite TTL relativo o expiración absoluta."""

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
    """Motivo opcional que acompaña al borrado lógico de un memory item."""

    reason: str = ""


class MemoryItemRecord(BaseModel):
    """Vista completa de un memory item, incluyendo versionado y ventana de validez."""

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
    """Envoltura de respuesta para operaciones sobre un único memory item."""

    memory_item: MemoryItemRecord = Field(alias="memoryItem")


class MemoryListResponse(BaseModel):
    """Listado de memory items activos de un proyecto."""

    memory_items: list[MemoryItemRecord] = Field(alias="memoryItems")


class RetrievalSearchRequest(BaseModel):
    """Consulta de búsqueda semántica acotada a un proyecto, con tope de resultados."""

    project_id: str = Field(alias="projectId")
    query: str = ""
    limit: int = 5


class RetrievalSearchResultRecord(BaseModel):
    """Un memory item recuperado junto a su puntaje de similitud."""

    score: float
    memory_item: MemoryItemRecord = Field(alias="memoryItem")


class RetrievalSearchResponse(BaseModel):
    """Resultado de la búsqueda; status/reason explican por qué viene vacía si lo está."""

    status: str
    reason: str
    results: list[RetrievalSearchResultRecord]


class RetrievalIndexSummary(BaseModel):
    """Estado del índice por proyecto tras un rebuild: backend usado, dimensiones e ids."""

    status: str
    reason: str
    project_id: str = Field(alias="projectId")
    backend: str
    degraded: bool
    dimensions: int
    ids: list[str]
    indexed: int


class RetrievalReindexRequest(BaseModel):
    """Petición de reconstrucción del índice vectorial de un proyecto."""

    project_id: str = Field(alias="projectId")


class RetrievalReindexResponse(BaseModel):
    """Envoltura de respuesta con el resumen del índice reconstruido."""

    index: RetrievalIndexSummary
