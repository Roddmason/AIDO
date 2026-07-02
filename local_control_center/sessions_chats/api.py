"""Endpoints HTTP legacy de sesiones y chats: solo lectura bajo `/api/v1/legacy`.

`sessions` y `chats` fueron reemplazados por `project_threads`/`thread_messages`.
Este router existe solo para consultar historial migrado; no monta rutas POST ni rutas activas
sin el prefijo legacy.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .models import (
    ChatsListResponse,
    SessionsListResponse,
)
from .repository import SessionsChatsRepository


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Construye el router legacy de sesiones/chats enlazado a la conexión de `platform`.

    `require_write` se acepta para mantener la firma de composición uniforme, pero no se usa:
    las rutas legacy son read-only.
    """
    _ = require_write
    router = APIRouter()

    def repository() -> SessionsChatsRepository:
        return SessionsChatsRepository(platform.connection)

    @router.get("/api/v1/legacy/sessions", response_model=SessionsListResponse)
    async def list_sessions() -> dict[str, Any]:
        return {"sessions": repository().list_sessions()}

    @router.get("/api/v1/legacy/chats", response_model=ChatsListResponse)
    async def list_chats() -> dict[str, Any]:
        return {"chats": repository().list_chats()}

    return router
