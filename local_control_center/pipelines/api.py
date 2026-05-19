from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.shared.event_bus import EventBus

from .repository import PipelinesRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> PipelinesRepository:
        return PipelinesRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/pipelines")
    async def list_pipelines() -> dict[str, Any]:
        return {"pipelines": repository().list_pipelines()}

    @router.post("/api/v1/pipelines", status_code=201)
    async def create_pipeline(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        pipeline = repository().create_pipeline(
            project_id=body["projectId"],
            title=body.get("title") or "Pipeline",
            session_id=body.get("sessionId"),
            chat_id=body.get("chatId"),
            stages=body.get("stages"),
        )
        event_bus().record_event(
            project_id=pipeline["projectId"],
            event_type="pipeline.created",
            payload={"pipelineId": pipeline["id"], "sessionId": pipeline["sessionId"], "chatId": pipeline["chatId"]},
        )
        return {"pipeline": pipeline}

    return router
