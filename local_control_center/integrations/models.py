"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

McpTransport = Literal["stdio"]


class McpServerRegisterRequest(BaseModel):
    id: str
    command: str
    transport: McpTransport = "stdio"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("transport", mode="before")
    @classmethod
    def normalize_transport(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class McpServerRecord(BaseModel):
    id: str
    command: str
    transport: McpTransport
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class McpServerResponse(BaseModel):
    mcp_server: McpServerRecord = Field(alias="mcpServer")


class IntegrationRecord(BaseModel):
    id: str
    kind: str
    status: str
    config: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class IntegrationsListResponse(BaseModel):
    integrations: list[IntegrationRecord]
    mcp_servers: list[McpServerRecord] = Field(alias="mcpServers")
    optional_adapters: dict[str, Any] = Field(alias="optionalAdapters")


class OpenDesignResponse(BaseModel):
    status: str
    backend: str
    runtime: str


class IdeConnectionUpsertRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    editor: str = "unknown"
    workspace_root: str | None = Field(default=None, alias="workspaceRoot")
    status: str = "connected"
    open_files: list[Any] = Field(default_factory=list, alias="openFiles")
    diagnostics: list[Any] = Field(default_factory=list)
    selection: dict[str, Any] = Field(default_factory=dict)
    terminal_context: dict[str, Any] = Field(default_factory=dict, alias="terminalContext")


class IdeConnectionRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    editor: str
    workspace_root: str = Field(alias="workspaceRoot")
    status: str
    open_files: list[Any] = Field(alias="openFiles")
    diagnostics: list[Any]
    selection: dict[str, Any]
    terminal_context: dict[str, Any] = Field(alias="terminalContext")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class IdeConnectionResponse(BaseModel):
    ide_connection: IdeConnectionRecord = Field(alias="ideConnection")


class IdeConnectionsListResponse(BaseModel):
    ide_connections: list[IdeConnectionRecord] = Field(alias="ideConnections")
