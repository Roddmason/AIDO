"""Esquemas Pydantic del contrato HTTP de sesiones y chats (entrada y salida).

Define la forma validada de cada request y la envoltura de cada response del slice.
Las claves del API viajan en camelCase (`projectId`, `teamId`, `sessionId`) vía `alias`,
mientras el código Python conserva snake_case; el repositorio ya emite ese camelCase.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    """Cuerpo para abrir una sesión; solo `projectId` es obligatorio."""

    project_id: str = Field(alias="projectId")
    name: str | None = None
    team_id: str | None = Field(default=None, alias="teamId")


class SessionRecord(BaseModel):
    """Sesión persistida tal como se expone al cliente (camelCase, con timestamps y estado)."""

    id: str
    project_id: str = Field(alias="projectId")
    team_id: str | None = Field(default=None, alias="teamId")
    name: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SessionResponse(BaseModel):
    """Envoltura de la sesión devuelta tras crearla."""

    session: SessionRecord


class SessionsListResponse(BaseModel):
    """Listado de sesiones devuelto por el endpoint de consulta."""

    sessions: list[SessionRecord]


class ChatCreateRequest(BaseModel):
    """Cuerpo para crear un chat; el `prompt` es obligatorio y el `sessionId` opcional."""

    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    prompt: str
    title: str | None = None


class ChatRecord(BaseModel):
    """Chat persistido tal como se expone al cliente, con su prompt y título resuelto."""

    id: str
    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    title: str
    prompt: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class ChatResponse(BaseModel):
    """Envoltura del chat devuelto tras crearlo."""

    chat: ChatRecord


class ChatsListResponse(BaseModel):
    """Listado de chats devuelto por el endpoint de consulta."""

    chats: list[ChatRecord]
