"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from . import commands
from .models import I18nCatalog, I18nCatalogResponse
from .repository import I18nRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> I18nRepository:
        return I18nRepository(platform.connection)

    @router.get("/api/v1/i18n/catalog", response_model=I18nCatalogResponse)
    async def get_i18n_catalog() -> dict[str, Any]:
        return commands.get_catalog(repository())

    @router.put("/api/v1/i18n/catalog", response_model=I18nCatalogResponse)
    async def put_i18n_catalog(body: I18nCatalog, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.replace_catalog(repository(), body.model_dump(by_alias=True))

    return router
