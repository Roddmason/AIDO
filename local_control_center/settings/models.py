"""Pydantic contracts for the Settings HTTP API (camelCase on the wire).

Defines ``ResolvedSetting`` (a single resolved key with value, origin, inheritance metadata),
``SettingsResponse`` (the full two-scope snapshot returned by GET), and ``SetSettingRequest``
(the write body for PUT). All models use the ``_Aliased`` base to support both Python
snake_case field access and camelCase wire serialization, mirroring the team_activity slice.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Aliased(BaseModel):
    """Base allowing population by field name or camelCase alias for both read and write."""

    model_config = ConfigDict(populate_by_name=True)


class ResolvedSetting(_Aliased):
    """A single platform setting resolved with its effective value, origin, and inheritance state."""

    key: str
    section: str
    project_section: str | None = Field(default=None, alias="projectSection")
    type: str
    enum: list[str] | None = None
    value: Any
    origin: Literal["default", "general", "project"]
    inherited: bool
    source: Literal["default", "general", "project"]
    editable_scopes: list[str] = Field(alias="editableScopes")


class SettingsResponse(_Aliased):
    """The full resolved settings snapshot: all settings at general scope + project overrides."""

    general: list[ResolvedSetting]
    project: list[ResolvedSetting]


class SetSettingRequest(_Aliased):
    """Request body for PUT /api/v1/settings/{key}: the scope, optional project id, and new value."""

    scope: Literal["general", "project"]
    scope_id: str | None = Field(default=None, alias="scopeId")
    value: Any
