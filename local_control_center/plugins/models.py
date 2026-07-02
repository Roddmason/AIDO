"""Pydantic contracts for the formal plugins HTTP API.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Aliased(BaseModel):
    """Base model allowing snake_case code and camelCase wire format."""

    model_config = ConfigDict(populate_by_name=True)


class PluginInstallLocalRequest(_Aliased):
    """Request body for installing a local plugin directory."""

    path: str


class PluginPermissionRecord(_Aliased):
    """Persisted explicit plugin permission declaration."""

    id: str
    permission: str
    risk_level: str = Field(alias="riskLevel")
    status: str
    reason: str
    created_at: str = Field(alias="createdAt")


class PluginSkillRecord(_Aliased):
    """Persisted SKILL.md contract declaration for a plugin version."""

    id: str
    path: str
    contract_hash: str = Field(alias="contractHash")
    status: str
    created_at: str = Field(alias="createdAt")


class PluginAgentRecord(_Aliased):
    """Persisted agent contract declaration for a plugin version."""

    id: str
    role: str
    capabilities: list[str]
    schema_: dict[str, Any] = Field(alias="schema")
    status: str
    created_at: str = Field(alias="createdAt")


class PluginToolRecord(_Aliased):
    """Persisted ToolBroker-routed tool declaration for a plugin version."""

    id: str
    name: str
    broker_tool: str = Field(alias="brokerTool")
    policy_required: bool = Field(alias="policyRequired")
    policy: Any
    schema_: dict[str, Any] = Field(alias="schema")
    status: str
    created_at: str = Field(alias="createdAt")


class PluginVersionRecord(_Aliased):
    """Active plugin version contract, including normalized child declarations."""

    id: str
    plugin_id: str = Field(alias="pluginId")
    version: str
    manifest_path: str = Field(alias="manifestPath")
    manifest_hash: str = Field(alias="manifestHash")
    package_hash: str = Field(alias="packageHash")
    min_aido_version: str = Field(alias="minAidoVersion")
    entrypoints: dict[str, Any]
    checksums: dict[str, str]
    manifest: dict[str, Any]
    status: str
    installed_at: str = Field(alias="installedAt")
    validated_at: str = Field(alias="validatedAt")
    permissions: list[PluginPermissionRecord]
    skills: list[PluginSkillRecord]
    agents: list[PluginAgentRecord]
    tools: list[PluginToolRecord]


class PluginRecord(_Aliased):
    """Installed plugin summary plus the active version contract."""

    id: str
    name: str
    publisher: str
    trust_level: str = Field(alias="trustLevel")
    status: str
    active_version_id: str | None = Field(alias="activeVersionId")
    capabilities: list[str]
    permissions: list[str]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    active_version: PluginVersionRecord | None = Field(alias="activeVersion")


class PluginResponse(_Aliased):
    """Single plugin response wrapper."""

    plugin: PluginRecord


class PluginsListResponse(_Aliased):
    """List response wrapper for installed plugins."""

    plugins: list[PluginRecord]


class PluginValidationResponse(_Aliased):
    """Result of validating an installed plugin version."""

    valid: bool
    issues: list[str]
    plugin: PluginRecord | None = None


class PluginScanLocalRequest(_Aliased):
    """Request body for scanning a local folder for plugin candidates."""

    path: str


class PluginScanCandidateRecord(_Aliased):
    """One plugin candidate directory found during a local folder scan."""

    path: str
    valid: bool
    issues: list[str]
    id: str | None = None
    name: str | None = None
    version: str | None = None
    publisher: str | None = None
    trust_level: str | None = Field(alias="trustLevel", default=None)
    permissions: list[str] = Field(default_factory=list)
    skills_count: int = Field(alias="skillsCount", default=0)
    agents_count: int = Field(alias="agentsCount", default=0)
    tools_count: int = Field(alias="toolsCount", default=0)
    installed: bool = False
    installed_status: str | None = Field(alias="installedStatus", default=None)


class PluginScanLocalResponse(_Aliased):
    """Scan result wrapper: normalized root plus discovered candidates."""

    root: str
    candidates: list[PluginScanCandidateRecord]


class PluginInstallEventRecord(_Aliased):
    """Immutable install lifecycle event (install, enable, disable, validate, or blocked)."""

    id: str
    plugin_id: str | None = Field(alias="pluginId", default=None)
    plugin_version_id: str | None = Field(alias="pluginVersionId", default=None)
    action: str
    status: str
    reason: str
    manifest_hash: str | None = Field(alias="manifestHash", default=None)
    payload: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class PluginInstallEventsResponse(_Aliased):
    """List response wrapper for plugin install lifecycle events."""

    events: list[PluginInstallEventRecord]
