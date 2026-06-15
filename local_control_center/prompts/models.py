"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PromptUpsertRequest(BaseModel):
    id: str | None = None
    project_id: str = Field(alias="projectId")
    name: str
    body: str
    mode: str = "manual"
    optimizer: str = ""
    applies_to: dict[str, Any] = Field(default_factory=dict, alias="appliesTo")


class PromptTemplateRecord(BaseModel):
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
    prompt_template: PromptTemplateRecord = Field(alias="promptTemplate")


class PromptTemplatesListResponse(BaseModel):
    prompt_templates: list[PromptTemplateRecord] = Field(alias="promptTemplates")
