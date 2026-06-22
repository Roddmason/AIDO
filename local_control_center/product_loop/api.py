"""Expone el endpoint HTTP de solo lectura del product loop sobre FastAPI.

Agrega, por proyecto y en una sola lectura para el Workbench, el estado durable del loop y su bitácora
(slice product_loop), las preguntas de clarificación, el brief vivo, los supuestos y las decisiones
(slice product_discovery) y el backlog —épicas, historias, tareas e iteraciones— (slice backlog). No
contiene lógica de negocio: delega en los repositorios. Es solo lectura scoped por proyecto, por lo que
no expone mutaciones ni exige el token de escritura.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository

from .models import ProductLoopStateResponse
from .repository import ProductLoopRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:  # noqa: ARG001
    """Arma el router del product loop.

    Mantiene la firma uniforme de los routers del control plane (``require_write`` se inyecta a todos),
    pero este slice es de solo lectura y no protege ninguna ruta, por eso el guard no se usa aquí.
    """
    router = APIRouter()

    def product_loop_repository() -> ProductLoopRepository:
        return ProductLoopRepository(platform.connection)

    def discovery_repository() -> ProductDiscoveryRepository:
        return ProductDiscoveryRepository(platform.connection)

    def backlog_repository() -> BacklogRepository:
        return BacklogRepository(platform.connection)

    @router.get(
        "/api/v1/projects/{project_id}/product-loop",
        response_model=ProductLoopStateResponse,
    )
    async def get_product_loop_state(project_id: str) -> dict[str, Any]:
        loops_repo = product_loop_repository()
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

    return router
