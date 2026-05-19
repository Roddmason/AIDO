from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from ..agents.openhands_adapter import openhands_status
from ..agents.swe_agent_adapter import swe_agent_status
from ..projects.repository import ProjectsRepository
from ..shared.event_bus import EventBus
from .mcp_gateway import mcp_gateway_status
from .repository import IntegrationsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> IntegrationsRepository:
        return IntegrationsRepository(platform.connection)

    def projects() -> ProjectsRepository:
        return ProjectsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/ide-connections")
    async def list_ide_connections() -> dict[str, list[Any]]:
        return {"ideConnections": repository().list_ide_connections()}

    @router.get("/api/v1/integrations")
    async def list_integrations() -> dict[str, Any]:
        return {
            "integrations": repository().list_integrations(),
            "mcpServers": repository().list_mcp_servers(),
            "optionalAdapters": {
                "mcp": mcp_gateway_status(),
                "openhands": openhands_status(),
                "sweAgent": swe_agent_status(),
            },
        }

    @router.post("/api/v1/integrations/mcp/register", status_code=201)
    async def register_mcp_server(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        server = repository().register_mcp_server(
            server_id=body["id"],
            command=body["command"],
            transport=body.get("transport", "stdio"),
            metadata=body.get("metadata") or {},
        )
        event_bus().record_event(event_type="mcp.server.registered", payload={"mcpServerId": server["id"]})
        event_bus().record_audit(
            action="mcp.server.register",
            target=server["id"],
            payload={"transport": server["transport"], "status": server["status"]},
        )
        return {"mcpServer": server}

    @router.post("/api/v1/ide-connections", status_code=201)
    async def upsert_ide_connection(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        project = projects().get_project(body["projectId"])
        connection = repository().upsert_ide_connection(
            project_id=project["id"],
            editor=body.get("editor", "unknown"),
            workspace_root=body.get("workspaceRoot") or body.get("workspace_root") or project["path"],
            status=body.get("status", "connected"),
            open_files=body.get("openFiles") or [],
            diagnostics=body.get("diagnostics") or [],
            selection=body.get("selection") or {},
            terminal_context=body.get("terminalContext") or {},
        )
        event_bus().record_event(
            project_id=project["id"],
            event_type="ide.connection.upserted",
            payload={"ideConnectionId": connection["id"]},
        )
        return {"ideConnection": connection}

    @router.get("/api/v1/open-design")
    async def open_design() -> dict[str, Any]:
        return {"status": "python-backend", "backend": "fastapi", "runtime": "windows-native"}

    return router
