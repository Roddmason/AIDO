"""Router HTTP de threads: lista y crea hilos, expone su timeline y ejecuta el coordinator por mensaje.

Lecturas (sin token): listar hilos por proyecto y obtener un hilo con su timeline completo (mensajes,
artifacts, decisiones y eventos). Mutaciones (token de escritura): crear un hilo, publicar un mensaje de
usuario —que dispara el ``ThreadCoordinator`` (responde o bloquea)— y resolver una decisión pendiente.
No contiene lógica de negocio: delega en ``ThreadsRepository`` y ``ThreadCoordinator``. Mapea
``KeyError→404`` y ``ValueError→422``; los errores no controlados los captura el handler global de la app.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .contracts import (
    ThreadCreateRequest,
    ThreadDecisionResolveRequest,
    ThreadDecisionResolveResponse,
    ThreadDetailResponse,
    ThreadEventsResponse,
    ThreadListResponse,
    ThreadMessageRequest,
    ThreadMessageResultResponse,
)
from .coordinator import ThreadCoordinator
from .repository import ThreadsRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router de threads: dos lecturas y tres mutaciones protegidas por token."""
    router = APIRouter()

    def repository() -> ThreadsRepository:
        return ThreadsRepository(platform.connection)

    def coordinator() -> ThreadCoordinator:
        return ThreadCoordinator(platform.connection, root=getattr(platform, "cwd", None))

    def thread_detail(thread_id: str) -> dict[str, Any]:
        repo = repository()
        thread = repo.get_thread(thread_id)
        return {
            "thread": thread,
            "messages": repo.list_messages(thread_id),
            "artifacts": repo.list_artifacts(thread_id),
            "decisions": repo.list_decisions(thread_id),
            "events": repo.list_events(thread_id),
        }

    @router.get("/api/v1/threads", response_model=ThreadListResponse)
    async def list_threads(
        projectId: str | None = None,
        ownerType: str | None = None,
        ownerId: str | None = None,
    ) -> dict[str, Any]:
        """Lista los hilos (headers) filtrando por proyecto y, opcionalmente, por entidad dueña."""
        return {
            "threads": repository().list_threads(project_id=projectId, owner_type=ownerType, owner_id=ownerId)
        }

    @router.post("/api/v1/threads", status_code=201, response_model=ThreadDetailResponse)
    async def create_thread(body: ThreadCreateRequest, request: Request) -> dict[str, Any]:
        """Crea un hilo enlazado a una entidad dueña del proyecto y devuelve su detalle vacío."""
        require_write(request)
        try:
            thread = repository().create_thread(
                project_id=body.project_id,
                owner_type=body.owner_type,
                owner_id=body.owner_id,
                title=body.title,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return thread_detail(thread["id"])

    @router.get("/api/v1/threads/{thread_id}", response_model=ThreadDetailResponse)
    async def get_thread(thread_id: str) -> dict[str, Any]:
        """Devuelve un hilo con su timeline completo (mensajes, artifacts, decisiones y eventos)."""
        try:
            return thread_detail(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/v1/threads/{thread_id}/events", response_model=ThreadEventsResponse)
    async def thread_events(thread_id: str, afterSeq: int = 0, limit: int = 300) -> dict[str, Any]:
        """Devuelve eventos nuevos del hilo para una consola incremental sin bloquear el chat."""
        try:
            repo = repository()
            thread = repo.get_thread(thread_id)
            events = repo.list_events_after(thread_id, after_sequence=afterSeq, limit=limit)
            last_seq = events[-1]["sequence"] if events else int(afterSeq)
            return {
                "events": events,
                "lastSeq": last_seq,
                "threadStatus": thread["status"],
                "running": thread["status"] in {"queued", "running"},
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/messages",
        response_model=ThreadMessageResultResponse,
    )
    async def post_message(thread_id: str, body: ThreadMessageRequest, request: Request) -> dict[str, Any]:
        """Publica un mensaje de usuario y ejecuta el coordinator: responde o bloquea con una decisión."""
        require_write(request)
        try:
            return coordinator().post_message(
                thread_id=thread_id,
                content=body.content,
                author=body.author or "user",
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/decisions/{decision_id}/resolve",
        response_model=ThreadDecisionResolveResponse,
    )
    async def resolve_decision(
        thread_id: str,
        decision_id: str,
        body: ThreadDecisionResolveRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Resuelve una decisión pendiente del hilo y lo reabre en ``open``."""
        require_write(request)
        try:
            return coordinator().resolve_decision(
                thread_id=thread_id,
                decision_id=decision_id,
                resolution=body.resolution,
                decided_by=body.decided_by,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
