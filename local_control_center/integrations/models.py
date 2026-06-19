"""Contratos Pydantic (camelCase en el borde HTTP) del slice de integraciones.

Define los request/response de conexiones IDE y servidores MCP, y los records que el
repositorio serializa hacia la API. Los alias mapean snake_case interno a camelCase del JSON.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

McpTransport = Literal["stdio"]


class McpServerRegisterRequest(BaseModel):
    """Cuerpo para registrar/actualizar un servidor MCP por su comando de arranque stdio."""

    id: str
    command: str
    transport: McpTransport = "stdio"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("transport", mode="before")
    @classmethod
    def normalize_transport(cls, value: Any) -> Any:
        """Normaliza el transporte a minúsculas para que el matching contra el allowlist sea estable."""
        return value.lower() if isinstance(value, str) else value


class McpServerRecord(BaseModel):
    """Servidor MCP persistido tal como se devuelve a la API, con timestamps de registro."""

    id: str
    command: str
    transport: McpTransport
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class McpServerResponse(BaseModel):
    """Respuesta de registro: envuelve el servidor MCP recién persistido."""

    mcp_server: McpServerRecord = Field(alias="mcpServer")


class IntegrationRecord(BaseModel):
    """Integración externa configurada (kind + estado + config arbitraria) expuesta a la API."""

    id: str
    kind: str
    status: str
    config: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class IntegrationsListResponse(BaseModel):
    """Vista agregada del slice: integraciones, servidores MCP y estado de adaptadores opcionales."""

    integrations: list[IntegrationRecord]
    mcp_servers: list[McpServerRecord] = Field(alias="mcpServers")
    optional_adapters: dict[str, Any] = Field(alias="optionalAdapters")


class OpenDesignResponse(BaseModel):
    """Sonda de disponibilidad del backend Open Design (status/backend/runtime)."""

    status: str
    backend: str
    runtime: str


class IdeConnectionUpsertRequest(BaseModel):
    """Cuerpo para crear o actualizar la conexión de un editor con su contexto de workspace."""

    project_id: str = Field(alias="projectId")
    editor: str = "unknown"
    workspace_root: str | None = Field(default=None, alias="workspaceRoot")
    status: str = "connected"
    open_files: list[Any] = Field(default_factory=list, alias="openFiles")
    diagnostics: list[Any] = Field(default_factory=list)
    selection: dict[str, Any] = Field(default_factory=dict)
    terminal_context: dict[str, Any] = Field(default_factory=dict, alias="terminalContext")


class IdeConnectionRecord(BaseModel):
    """Conexión IDE persistida: editor, raíz de workspace y contexto vivo (archivos, diagnósticos)."""

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
    """Respuesta de upsert: envuelve la conexión IDE resultante."""

    ide_connection: IdeConnectionRecord = Field(alias="ideConnection")


class IdeConnectionsListResponse(BaseModel):
    """Listado de conexiones IDE conocidas por el control center."""

    ide_connections: list[IdeConnectionRecord] = Field(alias="ideConnections")
