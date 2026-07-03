"""Contratos Pydantic (camelCase en el borde HTTP) del slice de integraciones.

Define los request/response de conexiones IDE y servidores MCP, y los records que el
repositorio serializa hacia la API. Los alias mapean snake_case interno a camelCase del JSON.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

McpTransport = Literal["stdio"]
N8N_EVENT_TYPES = (
    "thread.created",
    "loop.blocked",
    "approval.required",
    "delivery.ready",
    "gitleaks.failed",
    "qa.failed",
    "research.completed",
)
N8nEventType = Literal[
    "thread.created",
    "loop.blocked",
    "approval.required",
    "delivery.ready",
    "gitleaks.failed",
    "qa.failed",
    "research.completed",
]


class IntegrationApiModel(BaseModel):
    """Base para contratos del slice de integraciones con aliases camelCase estables."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


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


class N8nWebhookTargetCreateRequest(IntegrationApiModel):
    """Configura un target outbound n8n acotado a proyecto y event allowlist."""

    project_id: str = Field(alias="projectId")
    url: str
    credential_ref: str = Field(alias="credentialRef")
    enabled: bool = True
    allowed_event_types: list[N8nEventType] = Field(alias="allowedEventTypes")
    metadata: dict[str, Any] = Field(default_factory=dict)


class N8nWebhookTargetRecord(IntegrationApiModel):
    """Target n8n persistido; expone solo referencia de credencial, nunca el token."""

    id: str
    project_id: str = Field(alias="projectId")
    url: str
    credential_ref: str = Field(alias="credentialRef")
    enabled: bool
    allowed_event_types: list[N8nEventType] = Field(alias="allowedEventTypes")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class N8nWebhookTargetResponse(IntegrationApiModel):
    """Respuesta de configuración de target n8n."""

    target: N8nWebhookTargetRecord


class N8nIntegrationStatusRecord(IntegrationApiModel):
    """Estado seguro de la integración n8n, sin exponer secretos."""

    configured: bool
    target_count: int = Field(alias="targetCount")
    enabled_target_count: int = Field(alias="enabledTargetCount")
    allowed_event_types: list[N8nEventType] = Field(alias="allowedEventTypes")
    event_allowlist: list[N8nEventType] = Field(alias="eventAllowlist")
    targets: list[N8nWebhookTargetRecord]


class N8nIntegrationStatusResponse(IntegrationApiModel):
    """Respuesta del estado n8n."""

    status: N8nIntegrationStatusRecord


class N8nEventEmitRequest(IntegrationApiModel):
    """Emite un evento soportado hacia n8n usando un target project-scoped."""

    project_id: str = Field(alias="projectId")
    event_type: N8nEventType = Field(alias="eventType")
    target_id: str | None = Field(default=None, alias="targetId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    payload: dict[str, Any] = Field(default_factory=dict)


class N8nEventTestRequest(IntegrationApiModel):
    """Envía un evento de prueba por un target n8n configurado."""

    project_id: str = Field(alias="projectId")
    target_id: str | None = Field(default=None, alias="targetId")
    event_type: N8nEventType = Field(default="thread.created", alias="eventType")
    payload: dict[str, Any] = Field(default_factory=dict)


class N8nEventDeliveryRecord(IntegrationApiModel):
    """Resultado persistido de una entrega outbound hacia n8n, con payloads redactados."""

    id: str
    target_id: str = Field(alias="targetId")
    project_id: str = Field(alias="projectId")
    event_type: N8nEventType = Field(alias="eventType")
    subject_id: str | None = Field(default=None, alias="subjectId")
    status: Literal["delivered", "failed"]
    status_code: int | None = Field(default=None, alias="statusCode")
    request_payload: dict[str, Any] = Field(alias="requestPayload")
    response_body: dict[str, Any] = Field(alias="responseBody")
    error: str
    created_at: str = Field(alias="createdAt")


class N8nEventDeliveryResponse(IntegrationApiModel):
    """Respuesta de emisión/test outbound hacia n8n."""

    delivery: N8nEventDeliveryRecord


class N8nInboundWebhookRequest(IntegrationApiModel):
    """Webhook inbound desde n8n: solo permite crear thread o loop mediante token scoped."""

    project_id: str = Field(alias="projectId")
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class N8nInboundWebhookResponse(IntegrationApiModel):
    """Resultado de un comando inbound permitido desde n8n."""

    accepted: bool
    action: Literal["create_thread", "create_loop", "add_message", "get_status"]
    thread: dict[str, Any] | None = None
    loop: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    status: dict[str, Any] | None = None
