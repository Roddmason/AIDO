"""Esquemas Pydantic de request/response del streaming de sesiones CLI.

Definen el contrato HTTP para arrancar una sesión CLI en streaming, leer su bitácora de eventos de forma
incremental y cancelarla. Solo modelan datos: traducen el ``snake_case`` de Python al ``camelCase`` del
frontend vía alias de campo y reflejan las claves del mapeador ``row_to_cli_session_event``.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CliSessionStartRequest(BaseModel):
    """Cuerpo para arrancar una sesión CLI en streaming dentro de un workspace."""

    workspace_id: str = Field(alias="workspaceId")
    argv: list[str]
    runtime: str | None = None
    agent_id: str | None = Field(default=None, alias="agentId")
    env_policy: dict[str, Any] | None = Field(default=None, alias="envPolicy")


class CliSessionStartResponse(BaseModel):
    """Respuesta del arranque: id de la sesión y su estado inicial ``running``."""

    id: str
    status: str
    started_at: str = Field(alias="startedAt")


class CliSessionEventRecord(BaseModel):
    """Un evento acotado de la bitácora de streaming de una sesión CLI."""

    id: str
    cli_session_id: str = Field(alias="cliSessionId")
    project_id: str | None = Field(default=None, alias="projectId")
    seq: int
    type: str
    payload: dict[str, Any]
    artifact_id: str | None = Field(default=None, alias="artifactId")
    created_at: str = Field(alias="createdAt")


class CliSessionEventsResponse(BaseModel):
    """Página incremental de eventos de una sesión, más su ``seq`` más alto y si la sesión sigue activa."""

    events: list[CliSessionEventRecord]
    latest_seq: int = Field(alias="latestSeq")
    running: bool


class CliSessionCancelResponse(BaseModel):
    """Resultado de cancelar una sesión: ``True`` si la cancelación se solicitó (la sesión estaba activa)."""

    cancelled: bool
