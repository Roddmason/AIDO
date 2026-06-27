"""Expone los endpoints HTTP del slice de proyectos sobre FastAPI.

Cablea cada ruta a su comando de aplicación, construye el repositorio y el bus de eventos
por petición desde la conexión de la plataforma, y aplica el guard de escritura en las
mutaciones (descubrir, crear, seleccionar directorio). No contiene lógica de negocio.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.shared.event_bus import EventBus

from . import commands
from .local_paths import select_directory_with_native_dialog
from .models import (
    AgentsListResponse,
    DirectoryPickerRequest,
    DirectoryPickerResponse,
    ProjectAssessmentRunResponse,
    ProjectAssessmentsListResponse,
    ProjectCreateRequest,
    ProjectDiscoveryRequest,
    ProjectDiscoveryResponse,
    ProjectFindingsListResponse,
    ProjectResponse,
    ProjectsListResponse,
    ProjectTemplatesResponse,
    ProvidersListResponse,
    TeamsListResponse,
)
from .repository import ProjectsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router de proyectos; ``require_write`` protege las rutas mutadoras."""
    router = APIRouter()

    def repository() -> ProjectsRepository:
        return ProjectsRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/project-templates", response_model=ProjectTemplatesResponse)
    async def project_templates() -> dict[str, Any]:
        return commands.list_project_templates(repository())

    @router.get("/api/v1/projects", response_model=ProjectsListResponse)
    async def projects() -> dict[str, Any]:
        return commands.list_projects(repository())

    @router.post("/api/v1/projects/discover", response_model=ProjectDiscoveryResponse)
    async def discover_project(body: ProjectDiscoveryRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return commands.discover_project(path=body.path)

    @router.post("/api/v1/projects", status_code=201, response_model=ProjectResponse)
    async def create_project(body: ProjectCreateRequest, request: Request) -> ProjectResponse:
        require_write(request)
        payload = commands.create_project(
            repository(),
            event_bus(),
            cwd=platform.cwd,
            body=body.model_dump(by_alias=True, exclude_none=True),
        )
        return ProjectResponse(project=payload["project"], auditEvent=payload.get("auditEvent"))

    @router.post("/api/v1/local-paths/select-directory", response_model=DirectoryPickerResponse)
    async def select_directory(body: DirectoryPickerRequest, request: Request) -> dict[str, Any]:
        require_write(request)
        return select_directory_with_native_dialog(title=body.title, initial_path=body.initial_path)

    @router.get("/api/v1/providers", response_model=ProvidersListResponse)
    async def providers() -> dict[str, Any]:
        return commands.list_providers(repository())

    @router.get("/api/v1/teams", response_model=TeamsListResponse)
    async def teams(projectId: str | None = None) -> dict[str, Any]:
        return commands.list_teams(repository(), project_id=projectId)

    @router.get("/api/v1/agents", response_model=AgentsListResponse)
    async def agents(teamId: str | None = None) -> dict[str, Any]:
        return commands.list_agents(repository(), team_id=teamId)

    @router.post(
        "/api/v1/projects/{project_id}/assessment",
        status_code=201,
        response_model=ProjectAssessmentRunResponse,
    )
    async def run_assessment(project_id: str, request: Request) -> dict[str, Any]:
        require_write(request)
        from local_control_center.agents.assessment_runner import ProjectAssessmentRunner

        try:
            return ProjectAssessmentRunner(platform.connection, root=platform.cwd).run(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get(
        "/api/v1/projects/{project_id}/assessments",
        response_model=ProjectAssessmentsListResponse,
    )
    async def list_assessments(project_id: str) -> dict[str, Any]:
        return commands.list_project_assessments(repository(), project_id=project_id)

    @router.get(
        "/api/v1/projects/{project_id}/findings",
        response_model=ProjectFindingsListResponse,
    )
    async def list_findings(project_id: str, category: str | None = None) -> dict[str, Any]:
        return commands.list_project_findings(repository(), project_id=project_id, category=category)

    return router
