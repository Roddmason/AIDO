"""Expone los endpoints HTTP del product loop sobre FastAPI: una lectura agregada y dos mutaciones.

La lectura agrega, por proyecto y en una sola llamada para el Workbench, el estado durable del loop y su
bitácora (slice product_loop), las preguntas de clarificación, el brief vivo, los supuestos y las
decisiones (slice product_discovery) y el backlog —épicas, historias, tareas e iteraciones— (slice
backlog). Las mutaciones arrancan un loop y lo avanzan por su FSM durable vía el ``ProductLoopCoordinator``;
exigen el token de escritura y validan la transición contra el mapa permitido. No contiene lógica de
negocio: delega en los repositorios y el coordinador.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository

from .coordinator import ProductLoopCoordinator, ProductLoopTransitionError
from .models import (
    ProductLoopResumeResponse,
    ProductLoopStartRequest,
    ProductLoopStateResponse,
    ProductLoopTransitionRequest,
)
from .repository import ProductLoopRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router del product loop: una lectura agregada y dos mutaciones protegidas por token."""
    router = APIRouter()

    def coordinator() -> ProductLoopCoordinator:
        return ProductLoopCoordinator(platform.connection)

    def discovery_repository() -> ProductDiscoveryRepository:
        return ProductDiscoveryRepository(platform.connection)

    def backlog_repository() -> BacklogRepository:
        return BacklogRepository(platform.connection)

    @router.get(
        "/api/v1/projects/{project_id}/product-loop",
        response_model=ProductLoopStateResponse,
    )
    async def get_product_loop_state(project_id: str) -> dict[str, Any]:
        loops_repo = ProductLoopRepository(platform.connection)
        discovery = discovery_repository()
        backlog = backlog_repository()
        loops = loops_repo.list_loops(project_id)
        briefs = discovery.list_product_briefs(project_id=project_id)
        return {
            "loops": loops,
            "transitions": loops_repo.list_transitions(loops[0]["id"]) if loops else [],
            "questions": discovery.list_clarification_questions(project_id=project_id),
            "brief": briefs[0] if briefs else None,
            "assumptions": discovery.list_assumptions(project_id=project_id),
            "decisions": discovery.list_product_decisions(project_id=project_id),
            "epics": backlog.list_epics(project_id),
            "stories": backlog.list_user_stories(project_id=project_id),
            "tasks": backlog.list_agent_tasks(project_id=project_id),
            "iterations": backlog.list_iterations(project_id),
        }

    @router.post(
        "/api/v1/projects/{project_id}/product-loop",
        status_code=201,
        response_model=ProductLoopResumeResponse,
    )
    async def start_product_loop(
        project_id: str, body: ProductLoopStartRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        engine = coordinator()
        loop = engine.start(
            project_id=project_id,
            title=body.title,
            initiative_id=body.initiative_id,
            context=body.context,
        )
        return engine.resume(loop["id"])

    @router.post(
        "/api/v1/projects/{project_id}/product-loop/{loop_id}/transition",
        response_model=ProductLoopResumeResponse,
    )
    async def transition_product_loop(
        project_id: str, loop_id: str, body: ProductLoopTransitionRequest, request: Request
    ) -> dict[str, Any]:
        require_write(request)
        engine = coordinator()
        try:
            loop = engine.get(loop_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if loop["projectId"] != project_id:
            raise HTTPException(status_code=404, detail=f"Product loop not found in project: {loop_id}")
        try:
            engine.transition(
                loop_id,
                to_state=body.to_state,
                reason=body.reason or "",
                trigger=body.trigger or "",
                expected_version=body.expected_version,
            )
        except ProductLoopTransitionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return engine.resume(loop_id)

    return router
