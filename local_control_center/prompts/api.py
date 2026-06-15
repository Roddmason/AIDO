"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from .models import PromptResponse, PromptTemplatesListResponse, PromptUpsertRequest
from .repository import PromptsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> PromptsRepository:
        return PromptsRepository(platform.connection)

    @router.get("/api/v1/prompts", response_model=PromptTemplatesListResponse)
    async def list_prompts() -> dict[str, Any]:
        return {"promptTemplates": repository().list_prompt_templates()}

    @router.post("/api/v1/prompts", status_code=201, response_model=PromptResponse)
    async def upsert_prompt(body: PromptUpsertRequest, request: Request) -> PromptResponse:
        require_write(request)
        prompt = repository().upsert_prompt_template(
            prompt_id=body.id,
            project_id=body.project_id,
            name=body.name,
            body=body.body,
            mode=body.mode,
            optimizer=body.optimizer,
            applies_to=body.applies_to,
        )
        return PromptResponse(promptTemplate=prompt)

    return router
