"""Endpoint HTTP legacy de pipelines: solo lectura bajo `/api/v1/legacy`.

`pipelines` fue reemplazado por `project_threads` + jobs/workflow events para intake y ejecucion.
Este router mantiene consulta historica de registros migrados; no monta POST ni rutas activas
sin el prefijo legacy.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .models import PipelinesListResponse
from .repository import PipelinesRepository


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Construye el router legacy de pipelines; `require_write` no aplica porque es read-only."""
    _ = require_write
    router = APIRouter()

    def repository() -> PipelinesRepository:
        return PipelinesRepository(platform.connection)

    @router.get("/api/v1/legacy/pipelines", response_model=PipelinesListResponse)
    async def list_pipelines() -> dict[str, Any]:
        return {"pipelines": repository().list_pipelines()}

    return router
