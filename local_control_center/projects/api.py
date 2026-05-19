from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.shared.event_bus import EventBus

from . import commands
from .repository import ProjectsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> ProjectsRepository:
        return ProjectsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/project-templates")
    async def project_templates() -> dict[str, Any]:
        return commands.list_project_templates(repository())

    @router.get("/api/v1/projects")
    async def projects() -> dict[str, Any]:
        return commands.list_projects(repository())

    @router.post("/api/v1/projects", status_code=201)
    async def create_project(request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.create_project(repository(), event_bus(), cwd=platform.cwd, body=await request.json())

    @router.get("/api/v1/providers")
    async def providers() -> dict[str, Any]:
        return commands.list_providers(repository())

    @router.get("/api/v1/teams")
    async def teams(projectId: str | None = None) -> dict[str, Any]:  # noqa: N803 - API query uses camelCase.
        return commands.list_teams(repository(), project_id=projectId)

    @router.get("/api/v1/agents")
    async def agents(teamId: str | None = None) -> dict[str, Any]:  # noqa: N803 - API query uses camelCase.
        return commands.list_agents(repository(), team_id=teamId)

    return router
