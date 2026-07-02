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
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request

from local_control_center.shared.event_bus import row_to_audit

from .contracts import (
    ThreadArchiveRequest,
    ThreadArchiveResponse,
    ThreadCreateRequest,
    ThreadDecisionResolveRequest,
    ThreadDecisionResolveResponse,
    ThreadDeleteRequest,
    ThreadDeleteResponse,
    ThreadDetailResponse,
    ThreadEventsResponse,
    ThreadListResponse,
    ThreadMemoryRecallResponse,
    ThreadMessageRequest,
    ThreadMessageResultResponse,
    ThreadSimilarityMarkRequest,
    ThreadSimilarityMarkResponse,
    ThreadSimilarityResponse,
    ThreadUpdateRequest,
)
from .coordinator import ThreadCoordinator
from .memory_recall import ThreadMemoryRecallService
from .repository import ThreadLifecycleError, ThreadsRepository
from .similarity import ThreadSimilarityService


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Arma el router de threads: lecturas libres y mutaciones protegidas por token."""
    router = APIRouter()

    def repository() -> ThreadsRepository:
        return ThreadsRepository(platform.connection)

    def coordinator() -> ThreadCoordinator:
        return ThreadCoordinator(platform.connection, root=getattr(platform, "cwd", None))

    def similarity_service() -> ThreadSimilarityService:
        return ThreadSimilarityService(platform.connection)

    def memory_recall_service() -> ThreadMemoryRecallService:
        return ThreadMemoryRecallService(platform.connection)

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

    def latest_thread_audit(thread_id: str, action: str, project_id: str) -> dict[str, Any]:
        rows = platform.connection.execute(
            """
            SELECT * FROM audit_events
            WHERE project_id = ? AND target = ? AND action = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (project_id, thread_id, action),
        ).fetchall()
        if not rows:
            raise RuntimeError(f"Missing audit event for {action} on {thread_id}")
        return row_to_audit(rows[0])

    @router.get("/api/v1/threads", response_model=ThreadListResponse)
    async def list_threads(
        projectId: str | None = None,
        ownerType: str | None = None,
        ownerId: str | None = None,
        includeArchived: bool = False,
        includeDeleted: bool = False,
    ) -> dict[str, Any]:
        """Lista los hilos (headers) filtrando por proyecto y, opcionalmente, por entidad dueña."""
        return {
            "threads": repository().list_threads(
                project_id=projectId,
                owner_type=ownerType,
                owner_id=ownerId,
                include_archived=includeArchived,
                include_deleted=includeDeleted,
            )
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

    @router.patch("/api/v1/threads/{thread_id}", response_model=ThreadDetailResponse)
    async def update_thread(
        thread_id: str,
        body: ThreadUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Actualiza campos editables del header del hilo; hoy solo permite renombrar."""
        require_write(request)
        try:
            if body.title is None:
                raise ValueError("Thread title is required")
            repository().rename_thread(thread_id, body.title, actor="operator")
            return thread_detail(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/v1/threads/similar", response_model=ThreadSimilarityResponse)
    async def find_similar_threads(
        projectId: str,
        query: str,
        includeDeleted: bool = False,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Busca hilos similares por texto libre dentro de un proyecto."""
        try:
            return {
                "candidates": similarity_service().find_similar(
                    project_id=projectId,
                    query=query,
                    include_deleted=includeDeleted,
                    limit=limit,
                )
            }
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/v1/threads/{thread_id}/similar", response_model=ThreadSimilarityResponse)
    async def find_similar_to_thread(
        thread_id: str,
        query: str | None = None,
        includeDeleted: bool = False,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Busca hilos similares al hilo fuente, excluyendo el propio thread."""
        try:
            thread = repository().get_thread(thread_id)
            return {
                "candidates": similarity_service().find_similar(
                    project_id=thread["projectId"],
                    query=query,
                    source_thread_id=thread_id,
                    include_deleted=includeDeleted,
                    limit=limit,
                )
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/similar/{candidate_id}/mark",
        response_model=ThreadSimilarityMarkResponse,
    )
    async def mark_similar_thread(
        thread_id: str,
        candidate_id: str,
        body: ThreadSimilarityMarkRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Registra la decisión operacional tomada sobre un candidato similar."""
        require_write(request)
        try:
            source = repository().get_thread(thread_id)
            event = similarity_service().mark_similarity(
                project_id=source["projectId"],
                source_thread_id=thread_id,
                candidate_thread_id=candidate_id,
                score=body.score,
                reason=body.reason,
                action=body.action,
            )
            return {"event": event}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/v1/threads/{thread_id}/memory", response_model=ThreadMemoryRecallResponse)
    async def thread_memory(thread_id: str, limit: int = 5) -> dict[str, Any]:
        """Devuelve la memoria agregada del hilo: similares, decisiones, evidencia, lecciones y performance."""
        try:
            return memory_recall_service().recall(thread_id=thread_id, limit=limit)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/v1/threads/{thread_id}", response_model=ThreadDetailResponse)
    async def get_thread(thread_id: str) -> dict[str, Any]:
        """Devuelve un hilo con su timeline completo (mensajes, artifacts, decisiones y eventos)."""
        try:
            return thread_detail(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/archive",
        response_model=ThreadArchiveResponse,
    )
    async def archive_thread(
        thread_id: str,
        body: ThreadArchiveRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Archiva logicamente un hilo y deja auditoria explicita."""
        require_write(request)
        try:
            thread = repository().archive_thread(thread_id, reason=body.reason, actor=body.actor)
            return {
                "thread": thread,
                "auditEvent": latest_thread_audit(thread_id, "thread.archived", thread["projectId"]),
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/unarchive",
        response_model=ThreadArchiveResponse,
    )
    async def unarchive_thread(
        thread_id: str,
        body: ThreadArchiveRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Reabre un hilo archivado y registra el motivo."""
        require_write(request)
        try:
            thread = repository().unarchive_thread(thread_id, reason=body.reason, actor=body.actor)
            return {
                "thread": thread,
                "auditEvent": latest_thread_audit(thread_id, "thread.unarchived", thread["projectId"]),
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.delete(
        "/api/v1/threads/{thread_id}",
        response_model=ThreadDeleteResponse,
    )
    async def delete_thread(
        thread_id: str,
        request: Request,
        body: Annotated[ThreadDeleteRequest | None, Body()] = None,
    ) -> dict[str, Any]:
        """Borra logicamente un hilo; nunca elimina mensajes ni artifacts."""
        require_write(request)
        payload = body or ThreadDeleteRequest(reason="Operator requested thread deletion.")
        try:
            thread = repository().soft_delete_thread(thread_id, reason=payload.reason, actor=payload.actor)
            return {
                "thread": thread,
                "auditEvent": latest_thread_audit(thread_id, "thread.deleted", thread["projectId"]),
            }
        except ThreadLifecycleError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
                metadata=body.metadata,
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
