"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

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
    OpenDesignResponse,
)
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
    router = APIRouter()

    def repository() -> IntegrationsRepository:
        return IntegrationsRepository(platform.connection)

    def projects() -> ProjectsRepository:
        return ProjectsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/ide-connections", response_model=IdeConnectionsListResponse)
    async def list_ide_connections() -> dict[str, list[Any]]:
        return {"ideConnections": repository().list_ide_connections()}

    @router.get("/api/v1/integrations", response_model=IntegrationsListResponse)
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

    @router.post("/api/v1/integrations/mcp/register", status_code=201, response_model=McpServerResponse)
    async def register_mcp_server(body: McpServerRegisterRequest, request: Request) -> McpServerResponse:
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

    @router.post("/api/v1/ide-connections", status_code=201, response_model=IdeConnectionResponse)
    async def upsert_ide_connection(
        body: IdeConnectionUpsertRequest, request: Request
    ) -> IdeConnectionResponse:
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
        return {"status": "python-backend", "backend": "fastapi", "runtime": "windows-native"}

    return router
