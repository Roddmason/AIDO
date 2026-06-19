"""Esquemas Pydantic del contrato HTTP de pipelines (entrada de creación y formas de salida).

Definen el cuerpo aceptado al crear un pipeline y la representación serializada que devuelve
la API. Usan alias camelCase para hablar JSON con el frontend mientras el backend usa snake_case;
no contienen lógica de persistencia, solo validación y forma del payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PipelineCreateRequest(BaseModel):
    """Cuerpo del POST de creación: proyecto destino y vínculos/etapas opcionales."""

    project_id: str = Field(alias="projectId")
    title: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    stages: list[dict[str, Any]] | None = None


class PipelineRecord(BaseModel):
    """Pipeline ya persistido tal como se expone al cliente, con etapas y timestamps."""

    id: str
    project_id: str = Field(alias="projectId")
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    title: str
    status: str
    stages: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PipelineResponse(BaseModel):
    """Envoltura de un único pipeline para las respuestas de creación y detalle."""

    pipeline: PipelineRecord


class PipelinesListResponse(BaseModel):
    """Envoltura de la colección devuelta por el endpoint de listado."""

    pipelines: list[PipelineRecord]
