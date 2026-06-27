"""Esquemas de contrato HTTP del slice de prompts (request/response, camelCase externo).

Definen la forma de entrada y salida de la API de plantillas de prompts y traducen entre el
camelCase del cliente (alias Pydantic) y el snake_case interno usado por el repositorio.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PromptUpsertRequest(BaseModel):
    """Cuerpo del upsert: crea una plantilla nueva cuando `id` es nulo, o actualiza la existente."""

    id: str | None = None
    project_id: str = Field(alias="projectId")
    name: str
    body: str
    mode: str = "manual"
    optimizer: str = ""
    applies_to: dict[str, Any] = Field(default_factory=dict, alias="appliesTo")


class PromptTemplateRecord(BaseModel):
    """Plantilla persistida tal como se expone al cliente, incluyendo versión y marcas de tiempo."""

    id: str
    project_id: str = Field(alias="projectId")
    name: str
    mode: str
    body: str
    optimizer: str
    applies_to: dict[str, Any] = Field(alias="appliesTo")
    version: int
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PromptResponse(BaseModel):
    """Envoltura de respuesta para una única plantilla tras crearla o actualizarla."""

    prompt_template: PromptTemplateRecord = Field(alias="promptTemplate")


class PromptTemplatesListResponse(BaseModel):
    """Envoltura de respuesta para el listado completo de plantillas."""

    prompt_templates: list[PromptTemplateRecord] = Field(alias="promptTemplates")
