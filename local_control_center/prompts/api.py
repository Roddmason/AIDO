from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from .repository import PromptsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> PromptsRepository:
        return PromptsRepository(platform.connection)

    @router.get("/api/v1/prompts")
    async def list_prompts() -> dict[str, Any]:
        return {"promptTemplates": repository().list_prompt_templates()}

    @router.post("/api/v1/prompts", status_code=201)
    async def upsert_prompt(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        return {
            "promptTemplate": repository().upsert_prompt_template(
                prompt_id=body.get("id"),
                project_id=body["projectId"],
                name=body["name"],
                body=body["body"],
                mode=body.get("mode", "manual"),
                optimizer=body.get("optimizer", ""),
                applies_to=body.get("appliesTo") or {},
            )
        }

    return router
