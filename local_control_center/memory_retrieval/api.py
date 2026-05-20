from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Request

from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.schemas import RetrievalStatusResponse

from . import commands
from .index import RetrievalIndex
from .models import (
    EmptyObjectRequest,
    MemoryCreateRequest,
    MemoryResponse,
    RetrievalReindexResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from .repository import MemoryRepository

EMPTY_REINDEX_BODY = Body(default_factory=EmptyObjectRequest)


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def memory_repository() -> MemoryRepository:
        return MemoryRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    def retrieval_index() -> RetrievalIndex:
        return RetrievalIndex(memory=memory_repository(), index_dir=platform.db_path.parent / "faiss-index")

    @router.get("/api/v1/memory")
    async def list_memory() -> dict[str, Any]:
        return commands.list_memory(memory_repository())

    @router.post("/api/v1/memory", status_code=201, response_model=MemoryResponse)
    async def create_memory(body: MemoryCreateRequest, request: Request) -> MemoryResponse:
        require_write(request)
        payload = commands.create_memory(
            memory_repository(),
            event_bus(),
            body.model_dump(by_alias=True, exclude_none=True),
        )
        return MemoryResponse(memoryItem=payload["memoryItem"])

    @router.get("/api/v1/retrieval/status", response_model=RetrievalStatusResponse)
    async def retrieval_status() -> dict[str, Any]:
        return commands.retrieval_status(retrieval_index())

    @router.post("/api/v1/retrieval/reindex", status_code=202, response_model=RetrievalReindexResponse)
    async def retrieval_reindex(
        request: Request, body: EmptyObjectRequest = EMPTY_REINDEX_BODY
    ) -> RetrievalReindexResponse:
        require_write(request)
        _ = body
        payload = commands.retrieval_reindex(retrieval_index())
        return RetrievalReindexResponse(index=payload["index"])

    @router.post("/api/v1/retrieval/search", response_model=RetrievalSearchResponse)
    async def retrieval_search(body: RetrievalSearchRequest) -> RetrievalSearchResponse:
        payload = commands.retrieval_search(retrieval_index(), body.model_dump())
        return RetrievalSearchResponse(results=payload["results"])

    return router
