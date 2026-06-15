"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.shared.event_bus import EventBus

from .models import PipelineCreateRequest, PipelineResponse, PipelinesListResponse
from .repository import PipelinesRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> PipelinesRepository:
        return PipelinesRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/pipelines", response_model=PipelinesListResponse)
    async def list_pipelines() -> dict[str, Any]:
        return {"pipelines": repository().list_pipelines()}

    @router.post("/api/v1/pipelines", status_code=201, response_model=PipelineResponse)
    async def create_pipeline(body: PipelineCreateRequest, request: Request) -> PipelineResponse:
        require_write(request)
        pipeline = repository().create_pipeline(
            project_id=body.project_id,
            title=body.title or "Pipeline",
            session_id=body.session_id,
            chat_id=body.chat_id,
            stages=body.stages,
        )
        event_bus().record_event(
            project_id=pipeline["projectId"],
            event_type="pipeline.created",
            payload={"pipelineId": pipeline["id"], "sessionId": pipeline["sessionId"], "chatId": pipeline["chatId"]},
        )
        return PipelineResponse(pipeline=pipeline)

    return router
