"""Router HTTP del slice de integraciones: conexiones IDE y registro/listado de servidores MCP.

Expone los endpoints ``/api/v1/integrations``, ``/ide-connections``, ``/integrations/mcp/register``
y ``/open-design``; valida el registro MCP (defensa anti-inyección de shell) antes de persistir,
exige permiso de escritura en las mutaciones y emite eventos/auditoría tras cada cambio.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..agents.openhands_adapter import openhands_status
from ..agents.swe_agent_adapter import swe_agent_status
from ..projects.repository import ProjectsRepository
from ..shared.event_bus import EventBus
from .mcp_gateway import mcp_gateway_status
from .models import (
    IdeConnectionResponse,
    IdeConnectionsListResponse,
    IdeConnectionUpsertRequest,
    IntegrationsListResponse,
    McpServerRegisterRequest,
    McpServerResponse,
    N8nEventDeliveryResponse,
    N8nEventEmitRequest,
    N8nEventTestRequest,
    N8nInboundWebhookRequest,
    N8nInboundWebhookResponse,
    N8nIntegrationStatusResponse,
    N8nWebhookTargetCreateRequest,
    N8nWebhookTargetResponse,
    OpenDesignResponse,
)
from .n8n import (
    N8nAuthenticationError,
    N8nDeliveryError,
    N8nIntegrationError,
    N8nIntegrationService,
    N8nLocalRateLimiter,
    N8nRateLimitError,
    extract_inbound_token,
)
from .repository import IntegrationsRepository

MCP_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
MCP_SHELL_META = ("&&", "||", ";", "|", ">", "<", "`", "\n", "\r")
ALLOWED_MCP_TRANSPORTS = {"stdio"}


def validate_mcp_registration(body: dict[str, Any]) -> dict[str, Any]:
    """Sanea y valida el registro de un servidor MCP antes de persistirlo.

    Invariante de seguridad: el comando aceptado es argv-style de un solo proceso, sin
    metacaracteres de shell (``&&``, ``|``, ``;``, redirecciones, backticks, saltos de línea),
    el id cumple el patrón restringido y el transport está en el allowlist (``stdio``).

    Returns:
        El cuerpo normalizado con ``id``, ``command``, ``transport`` y ``metadata``.

    Raises:
        HTTPException: 422 ante id inválido, comando vacío/demasiado largo, comando con
            operadores de shell, transport no permitido o metadata que no es objeto.
    """
    server_id = str(body.get("id") or "").strip()
    if not MCP_SERVER_ID_RE.match(server_id):
        raise HTTPException(
            status_code=422,
            detail="id must use lowercase letters, numbers, dashes or underscores and be 3-64 characters.",
        )
    command = str(body.get("command") or "").strip()
    if not command:
        raise HTTPException(status_code=422, detail="command is required.")
    if len(command) > 512:
        raise HTTPException(status_code=422, detail="command must be 512 characters or fewer.")
    if any(token in command for token in MCP_SHELL_META):
        raise HTTPException(
            status_code=422, detail="command must be a single argv-style command without shell operators."
        )
    transport = str(body.get("transport") or "stdio").strip().lower()
    if transport not in ALLOWED_MCP_TRANSPORTS:
        raise HTTPException(status_code=422, detail="transport must be stdio in the MVP runtime.")
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata must be an object.")
    return {"id": server_id, "command": command, "transport": transport, "metadata": metadata}


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Construye el ``APIRouter`` del slice ligado a la conexión del ``platform`` y al guard de escritura."""
    router = APIRouter()

    def repository() -> IntegrationsRepository:
        """Repositorio de integraciones sobre la conexión activa del platform."""
        return IntegrationsRepository(platform.connection)

    def projects() -> ProjectsRepository:
        """Repositorio de proyectos, usado para resolver y validar el proyecto destino."""
        return ProjectsRepository(platform.connection)

    def event_bus() -> EventBus:
        """Bus de eventos/auditoría sobre la conexión activa del platform."""
        return EventBus(platform.connection)

    n8n_rate_limiter = N8nLocalRateLimiter()

    def n8n_service() -> N8nIntegrationService:
        """Servicio n8n con transporte HTTP inyectable para tests y runtime real por defecto."""
        return N8nIntegrationService(
            platform.connection,
            http_post=getattr(platform, "n8n_http_post", None),
            rate_limiter=getattr(platform, "n8n_rate_limiter", n8n_rate_limiter),
            rate_limit_config=getattr(platform, "n8n_inbound_rate_limit", None),
        )

    @router.get("/api/v1/ide-connections", response_model=IdeConnectionsListResponse)
    async def list_ide_connections() -> dict[str, list[Any]]:
        """Devuelve todas las conexiones IDE registradas."""
        return {"ideConnections": repository().list_ide_connections()}

    @router.get("/api/v1/integrations", response_model=IntegrationsListResponse)
    async def list_integrations() -> dict[str, Any]:
        """Devuelve integraciones, servidores MCP y el estado de los adaptadores opcionales."""
        return {
            "integrations": repository().list_integrations(),
            "mcpServers": repository().list_mcp_servers(),
            "optionalAdapters": {
                "mcp": mcp_gateway_status(),
                "openhands": openhands_status(),
                "sweAgent": swe_agent_status(),
            },
        }

    @router.get("/api/v1/integrations/n8n/status", response_model=N8nIntegrationStatusResponse)
    async def get_n8n_status(projectId: str | None = None) -> dict[str, Any]:
        """Devuelve estado de configuración n8n sin secretos, filtrado por proyecto si se pide."""
        try:
            return {"status": n8n_service().status(project_id=projectId)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/api/v1/integrations/mcp/register", status_code=201, response_model=McpServerResponse)
    async def register_mcp_server(body: McpServerRegisterRequest, request: Request) -> McpServerResponse:
        """Registra un servidor MCP validado; exige escritura y emite evento + auditoría."""
        require_write(request)
        payload = validate_mcp_registration(body.model_dump(by_alias=True))
        server = repository().register_mcp_server(
            server_id=payload["id"],
            command=payload["command"],
            transport=payload["transport"],
            metadata=payload["metadata"],
        )
        event_bus().record_event(event_type="mcp.server.registered", payload={"mcpServerId": server["id"]})
        event_bus().record_audit(
            action="mcp.server.register",
            target=server["id"],
            payload={"transport": server["transport"], "status": server["status"]},
        )
        return McpServerResponse(mcpServer=server)

    @router.post(
        "/api/v1/integrations/n8n/configure",
        status_code=201,
        response_model=N8nWebhookTargetResponse,
    )
    async def configure_n8n(body: N8nWebhookTargetCreateRequest, request: Request) -> dict[str, Any]:
        """Configura el target n8n usando la ruta canónica solicitada por el contrato."""
        require_write(request)
        try:
            target = n8n_service().configure_target(
                project_id=body.project_id,
                url=body.url,
                credential_ref=body.credential_ref,
                enabled=body.enabled,
                allowed_event_types=list(body.allowed_event_types),
                metadata=body.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"target": target}

    @router.post(
        "/api/v1/integrations/n8n/webhook-targets",
        status_code=201,
        response_model=N8nWebhookTargetResponse,
    )
    async def upsert_n8n_webhook_target(
        body: N8nWebhookTargetCreateRequest, request: Request
    ) -> dict[str, Any]:
        """Crea o actualiza un target n8n outbound acotado a proyecto, token y event allowlist."""
        require_write(request)
        try:
            target = n8n_service().configure_target(
                project_id=body.project_id,
                url=body.url,
                credential_ref=body.credential_ref,
                enabled=body.enabled,
                allowed_event_types=list(body.allowed_event_types),
                metadata=body.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"target": target}

    @router.post("/api/v1/integrations/n8n/test", response_model=N8nEventDeliveryResponse)
    async def test_n8n_target(body: N8nEventTestRequest, request: Request) -> dict[str, Any]:
        """Envía un evento de prueba por el mismo corredor outbound real hacia n8n."""
        require_write(request)
        try:
            delivery = n8n_service().test_target(
                project_id=body.project_id,
                target_id=body.target_id,
                event_type=body.event_type,
                payload=body.payload,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except N8nDeliveryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"delivery": delivery}

    @router.post("/api/v1/integrations/n8n/emit", response_model=N8nEventDeliveryResponse)
    async def emit_n8n(body: N8nEventEmitRequest, request: Request) -> dict[str, Any]:
        """Emit an allowlisted n8n event through the canonical contract route."""
        require_write(request)
        try:
            delivery = n8n_service().emit_event(
                project_id=body.project_id,
                target_id=body.target_id,
                event_type=body.event_type,
                subject_id=body.subject_id,
                payload=body.payload,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except N8nDeliveryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"delivery": delivery}

    @router.post("/api/v1/integrations/n8n/emit-event", response_model=N8nEventDeliveryResponse)
    async def emit_n8n_event(body: N8nEventEmitRequest, request: Request) -> dict[str, Any]:
        """Emitir un evento soportado hacia n8n si el target del proyecto lo permite."""
        require_write(request)
        try:
            delivery = n8n_service().emit_event(
                project_id=body.project_id,
                target_id=body.target_id,
                event_type=body.event_type,
                subject_id=body.subject_id,
                payload=body.payload,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except N8nDeliveryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"delivery": delivery}

    @router.post(
        "/api/v1/integrations/n8n/inbound/{token}",
        response_model=N8nInboundWebhookResponse,
    )
    async def receive_n8n_inbound(
        token: str,
        body: N8nInboundWebhookRequest,
        response: Response,
    ) -> dict[str, Any]:
        """Webhook inbound canónico: token scoped en path, acciones seguras y rate limit local."""
        try:
            result = n8n_service().handle_inbound(
                project_id=body.project_id,
                action=body.action,
                payload=body.payload,
                token=token,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nRateLimitError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except N8nAuthenticationError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        response.status_code = 200 if result["action"] == "get_status" else 201
        return result

    @router.post(
        "/api/v1/integrations/n8n/webhook",
        response_model=N8nInboundWebhookResponse,
    )
    async def receive_n8n_webhook(
        body: N8nInboundWebhookRequest, request: Request, response: Response
    ) -> dict[str, Any]:
        """Webhook inbound n8n: solo crea thread o loop con token scoped; nunca ejecuta comandos."""
        try:
            result = n8n_service().handle_inbound(
                project_id=body.project_id,
                action=body.action,
                payload=body.payload,
                token=extract_inbound_token(request.headers),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except N8nRateLimitError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except N8nAuthenticationError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except N8nIntegrationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        response.status_code = 200 if result["action"] == "get_status" else 201
        return result

    @router.post("/api/v1/ide-connections", status_code=201, response_model=IdeConnectionResponse)
    async def upsert_ide_connection(
        body: IdeConnectionUpsertRequest, request: Request
    ) -> IdeConnectionResponse:
        """Crea o actualiza una conexión IDE para un proyecto válido; exige escritura y emite evento."""
        require_write(request)
        project = projects().get_project(body.project_id)
        connection = repository().upsert_ide_connection(
            project_id=project["id"],
            editor=body.editor,
            workspace_root=body.workspace_root or project["path"],
            status=body.status,
            open_files=body.open_files,
            diagnostics=body.diagnostics,
            selection=body.selection,
            terminal_context=body.terminal_context,
        )
        event_bus().record_event(
            project_id=project["id"],
            event_type="ide.connection.upserted",
            payload={"ideConnectionId": connection["id"]},
        )
        return IdeConnectionResponse(ideConnection=connection)

    @router.get("/api/v1/open-design", response_model=OpenDesignResponse)
    async def open_design() -> dict[str, Any]:
        """Sonda de salud del backend Open Design (identifica runtime y framework)."""
        return {"status": "python-backend", "backend": "fastapi", "runtime": "windows-native"}

    return router
