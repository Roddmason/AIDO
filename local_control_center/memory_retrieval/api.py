from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from . import commands


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/memory")
    async def list_memory() -> dict[str, Any]:
        return commands.list_memory(platform)

    @router.post("/api/v1/memory", status_code=201)
    async def create_memory(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.create_memory(platform, await request.json())

    @router.get("/api/v1/retrieval/status")
    async def retrieval_status() -> dict[str, Any]:
        return commands.retrieval_status(platform)

    @router.post("/api/v1/retrieval/reindex", status_code=202)
    async def retrieval_reindex(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.retrieval_reindex(platform)

    @router.post("/api/v1/retrieval/search")
    async def retrieval_search(request: Request) -> dict[str, Any]:
        return commands.retrieval_search(platform, await request.json())

    return router
