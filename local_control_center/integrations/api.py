from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agents.openhands_adapter import openhands_status
from ..agents.swe_agent_adapter import swe_agent_status
from ..projects.repository import ProjectsRepository
from ..shared.event_bus import EventBus
from .mcp_gateway import mcp_gateway_status
from .repository import IntegrationsRepository


MCP_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
MCP_SHELL_META = ("&&", "||", ";", "|", ">", "<", "`", "\n", "\r")
ALLOWED_MCP_TRANSPORTS = {"stdio"}


def validate_mcp_registration(body: dict[str, Any]) -> dict[str, Any]:
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
        raise HTTPException(status_code=422, detail="command must be a single argv-style command without shell operators.")
    transport = str(body.get("transport") or "stdio").strip().lower()
    if transport not in ALLOWED_MCP_TRANSPORTS:
        raise HTTPException(status_code=422, detail="transport must be stdio in the MVP runtime.")
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata must be an object.")
    return {"id": server_id, "command": command, "transport": transport, "metadata": metadata}


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
        body = validate_mcp_registration(await request.json())
        server = repository().register_mcp_server(
            server_id=body["id"],
            command=body["command"],
            transport=body["transport"],
            metadata=body["metadata"],
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
