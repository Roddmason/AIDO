"""Esquemas Pydantic del contrato HTTP de sesiones y chats (entrada y salida).

Define la forma validada de cada request y la envoltura de cada response del slice.
Las claves del API viajan en camelCase (`projectId`, `teamId`, `sessionId`) vía `alias`,
mientras el código Python conserva snake_case; el repositorio ya emite ese camelCase.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class SessionsListResponse(BaseModel):
    """Listado de sesiones devuelto por el endpoint de consulta."""

    sessions: list[SessionRecord]


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


class ChatsListResponse(BaseModel):
    """Listado de chats devuelto por el endpoint de consulta."""

    chats: list[ChatRecord]
