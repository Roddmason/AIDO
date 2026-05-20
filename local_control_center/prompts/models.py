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


class PromptResponse(BaseModel):
    prompt_template: dict[str, Any] = Field(alias="promptTemplate")
