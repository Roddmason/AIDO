"""Router FastAPI del slice: endpoints HTTP de memoria y retrieval.

Expone el CRUD de memory items y las operaciones de índice (status, reindex,
search) sobre /api/v1, cableando cada handler con repositorio, event bus e
índice construidos por petición. Las mutaciones pasan por require_write; la
lógica vive en commands, este módulo solo traduce HTTP a casos de uso.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Query, Request

from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.schemas import RetrievalStatusResponse

from . import commands
from .index import RetrievalIndex
from .models import (
    MemoryCreateRequest,
    MemoryDeleteRequest,
    MemoryListResponse,
    MemoryResponse,
    RetrievalReindexRequest,
    RetrievalReindexResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from .repository import MemoryRepository

DELETE_MEMORY_BODY = Body(default_factory=MemoryDeleteRequest)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el APIRouter de memoria/retrieval ligado a la plataforma y al guard de escritura.

    El índice se persiste bajo db_path.parent / "faiss-index"; las rutas de
    creación, borrado y reindex exigen require_write antes de mutar.
    """
    router = APIRouter()

    def memory_repository() -> MemoryRepository:
        return MemoryRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    def retrieval_index() -> RetrievalIndex:
        return RetrievalIndex(memory=memory_repository(), index_dir=platform.db_path.parent / "faiss-index")

    @router.get("/api/v1/memory", response_model=MemoryListResponse)
    async def list_memory(project_id: str | None = Query(default=None, alias="projectId")) -> dict[str, Any]:
        return commands.list_memory(memory_repository(), project_id=project_id)

    @router.post("/api/v1/memory", status_code=201, response_model=MemoryResponse)
    async def create_memory(body: MemoryCreateRequest, request: Request) -> MemoryResponse:
        require_write(request)
        payload = commands.create_memory(
            memory_repository(),
            event_bus(),
            body.model_dump(by_alias=True, exclude_none=True),
        )
        return MemoryResponse(memoryItem=payload["memoryItem"])

    @router.delete("/api/v1/memory/{memory_id}", response_model=MemoryResponse)
    async def delete_memory(
        memory_id: str,
        request: Request,
        body: MemoryDeleteRequest = DELETE_MEMORY_BODY,
    ) -> MemoryResponse:
        require_write(request)
        payload = commands.delete_memory(
            memory_repository(),
            event_bus(),
            memory_id,
            body.model_dump(by_alias=True),
        )
        return MemoryResponse(memoryItem=payload["memoryItem"])

    @router.get("/api/v1/retrieval/status", response_model=RetrievalStatusResponse)
    async def retrieval_status(
        project_id: str | None = Query(default=None, alias="projectId"),
    ) -> dict[str, Any]:
        return commands.retrieval_status(retrieval_index(), project_id=project_id)

    @router.post("/api/v1/retrieval/reindex", status_code=202, response_model=RetrievalReindexResponse)
    async def retrieval_reindex(request: Request, body: RetrievalReindexRequest) -> RetrievalReindexResponse:
        require_write(request)
        payload = commands.retrieval_reindex(retrieval_index(), body.model_dump(by_alias=True))
        return RetrievalReindexResponse(index=payload["index"])

    @router.post("/api/v1/retrieval/search", response_model=RetrievalSearchResponse)
    async def retrieval_search(body: RetrievalSearchRequest) -> RetrievalSearchResponse:
        payload = commands.retrieval_search(retrieval_index(), body.model_dump(by_alias=True))
        return RetrievalSearchResponse(
            status=payload["status"],
            reason=payload["reason"],
            results=payload["results"],
        )

    return router
