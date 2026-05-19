from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.shared.event_bus import EventBus

from . import commands
from .index import RetrievalIndex
from .repository import MemoryRepository


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

    @router.post("/api/v1/memory", status_code=201)
    async def create_memory(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.create_memory(memory_repository(), event_bus(), await request.json())

    @router.get("/api/v1/retrieval/status")
    async def retrieval_status() -> dict[str, Any]:
        return commands.retrieval_status(retrieval_index())

    @router.post("/api/v1/retrieval/reindex", status_code=202)
    async def retrieval_reindex(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.retrieval_reindex(retrieval_index())

    @router.post("/api/v1/retrieval/search")
    async def retrieval_search(request: Request) -> dict[str, Any]:
        return commands.retrieval_search(retrieval_index(), await request.json())

    return router
