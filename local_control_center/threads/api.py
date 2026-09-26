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

from local_control_center.product_loop.eta import safe_estimate_thread_eta
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_team.configuration import write_thread_runtime_team
from local_control_center.runtime_team.facts import load_runtime_facts
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import row_to_audit

from .contracts import (
    ProjectFunctionalityResponse,
    ThreadArchiveRequest,
    ThreadArchiveResponse,
    ThreadCancelRequest,
    ThreadCancelResponse,
    ThreadCostPerformanceResponse,
    ThreadCreateRequest,
    ThreadDecisionBatchResolveRequest,
    ThreadDecisionBatchResolveResponse,
    ThreadDecisionResolveRequest,
    ThreadDecisionResolveResponse,
    ThreadDeleteRequest,
    ThreadDeleteResponse,
    ThreadDetailResponse,
    ThreadEventsResponse,
    ThreadListResponse,
    ThreadMemoryRecallResponse,
    ThreadMemoryReindexResponse,
    ThreadMessageRequest,
    ThreadMessageResultResponse,
    ThreadNoteRequest,
    ThreadNoteResponse,
    ThreadRunConfigurationRequest,
    ThreadRunConfigurationResponse,
    ThreadSimilarityMarkRequest,
    ThreadSimilarityMarkResponse,
    ThreadSimilarityResponse,
    ThreadUpdateRequest,
)
from .coordinator import ACTIVE_EXECUTION_STATUSES, ThreadBusyError, ThreadCoordinator
from .cost_performance import ThreadCostPerformanceService
from .memory_recall import ThreadMemoryRecallService
from .repository import ThreadLifecycleError, ThreadsRepository
from .similarity import ThreadMemoryService, ThreadSimilarityService

# El detalle del hilo viaja completo en cada apertura; estas cotas conservan solo el tail
# reciente de las colecciones que crecen sin techo. El stream incremental
# ``GET /threads/{id}/events`` (afterSeq + limit) sigue cubriendo el historial completo.
THREAD_DETAIL_MESSAGE_TAIL = 500
THREAD_DETAIL_EVENT_TAIL = 500
THREAD_DETAIL_ARTIFACT_TAIL = 300


def _combined_answer_text(
    *, resolution: str | None, selected_options: list[str], free_text: str | None
) -> str:
    """Resumen de texto compatible con los lectores existentes.

    Usa ``resolution`` si vino explícito, o las opciones elegidas seguidas del texto libre.
    """
    explicit = (resolution or "").strip()
    if explicit:
        return explicit
    parts = [option.strip() for option in selected_options if option.strip()]
    clean_free_text = (free_text or "").strip()
    if clean_free_text:
        parts.append(clean_free_text)
    return ", ".join(parts)


def _combined_resolution_text(body: ThreadDecisionResolveRequest) -> str:
    """Resumen de texto compatible con los lectores existentes.

    Usa el ``resolution`` explícito si vino, o las opciones elegidas seguidas del texto libre.
    """
    return _combined_answer_text(
        resolution=body.resolution, selected_options=body.selected_options, free_text=body.free_text
    )


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

    def cost_performance_service() -> ThreadCostPerformanceService:
        return ThreadCostPerformanceService(platform.connection)

    def thread_memory_service() -> ThreadMemoryService:
        return ThreadMemoryService(platform.connection)

    def thread_detail(thread_id: str) -> dict[str, Any]:
        repo = repository()
        thread = repo.get_thread(thread_id)
        return {
            "thread": thread,
            "messages": repo.list_messages(thread_id, limit=THREAD_DETAIL_MESSAGE_TAIL),
            "artifacts": repo.list_artifacts(thread_id, limit=THREAD_DETAIL_ARTIFACT_TAIL),
            "decisions": repo.list_decisions(thread_id),
            "events": repo.list_events(thread_id, limit=THREAD_DETAIL_EVENT_TAIL),
            "eta": safe_estimate_thread_eta(
                platform.connection, thread_id=thread_id, project_id=thread["projectId"]
            ),
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
        repo = repository()
        owner_id = body.owner_id
        if body.owner_type == "workspace":
            owner_id = repo.resolve_workspace_owner_id(project_id=body.project_id, owner_id=owner_id)
        try:
            thread = repo.create_thread(
                project_id=body.project_id,
                owner_type=body.owner_type,
                owner_id=owner_id,
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

    @router.patch(
        "/api/v1/threads/{thread_id}/run-configuration",
        response_model=ThreadRunConfigurationResponse,
    )
    def update_thread_run_configuration(
        thread_id: str,
        body: ThreadRunConfigurationRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Fija o limpia el equipo de runtimes del hilo; solo mientras el hilo no está en ejecución.

        Estado, escritura y evento van en un ``BEGIN IMMEDIATE``: serializa contra ``post_message``,
        que evalúa el gate y sella el run en su propia transacción inmediata.
        """
        require_write(request)
        try:
            thread = repository().get_thread(thread_id)
            facts = load_runtime_facts(platform.connection, project_id=thread["projectId"])
            with immediate_transaction(platform.connection):
                current = repository().get_thread(thread_id)
                if current["status"] in ACTIVE_EXECUTION_STATUSES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Thread {thread_id} is {current['status']}: change its AI team after the current run.",
                    )
                team = write_thread_runtime_team(
                    platform.connection,
                    thread_id=thread_id,
                    project_id=current["projectId"],
                    allowed_runtimes=body.allowed_runtimes,
                    role_runtimes=body.role_runtimes.model_dump(exclude_none=True),
                    facts=facts,
                )
                repository().record_event(
                    thread_id=thread_id, type="run_configuration_updated", payload={"runtimeTeam": team}
                )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"threadId": thread_id, "runtimeTeam": team}

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

    @router.post(
        "/api/v1/threads/{thread_id}/memory/reindex",
        response_model=ThreadMemoryReindexResponse,
    )
    async def reindex_thread_memory(thread_id: str, request: Request) -> dict[str, Any]:
        """Reconstruye índice de memoria y registry de funcionalidad para un hilo."""
        require_write(request)
        try:
            return thread_memory_service().reindex_thread_memory(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get(
        "/api/v1/projects/{project_id}/functionality",
        response_model=ProjectFunctionalityResponse,
    )
    async def project_functionality(
        project_id: str,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Lista funcionalidad existente del proyecto para evitar trabajo duplicado."""
        return {
            "functionality": thread_memory_service().list_project_functionality(
                project_id=project_id,
                query=query,
                limit=limit,
            )
        }

    @router.get("/api/v1/threads/{thread_id}", response_model=ThreadDetailResponse)
    async def get_thread(thread_id: str) -> dict[str, Any]:
        """Devuelve un hilo con su timeline completo (mensajes, artifacts, decisiones y eventos)."""
        try:
            return thread_detail(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get(
        "/api/v1/threads/{thread_id}/cost-performance",
        response_model=ThreadCostPerformanceResponse,
    )
    async def thread_cost_performance(thread_id: str) -> dict[str, Any]:
        """Devuelve el snapshot accionable de costo/rendimiento del hilo (unknown se modela como None, no $0)."""
        try:
            return {"costPerformance": cost_performance_service().snapshot(thread_id)}
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
        except ThreadLifecycleError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
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
                "eta": safe_estimate_thread_eta(
                    platform.connection, thread_id=thread_id, project_id=thread["projectId"]
                ),
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/messages",
        response_model=ThreadMessageResultResponse,
    )
    def post_message(thread_id: str, body: ThreadMessageRequest, request: Request) -> dict[str, Any]:
        """Publica un mensaje de usuario y ejecuta el coordinator: responde o bloquea con una decisión."""
        require_write(request)
        try:
            return coordinator().post_message(
                thread_id=thread_id,
                content=body.content,
                author=body.author or "user",
                metadata=body.metadata,
            )
        except ThreadBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/notes",
        response_model=ThreadNoteResponse,
    )
    async def post_note(thread_id: str, body: ThreadNoteRequest, request: Request) -> dict[str, Any]:
        """Adjunta una nota del operador al hilo sin encolar ejecución (escritura segura en ejecución)."""
        require_write(request)
        try:
            return coordinator().add_operator_note(
                thread_id=thread_id,
                content=body.content,
                author=body.author or "operator",
                metadata=body.metadata,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/api/v1/threads/{thread_id}/cancel",
        response_model=ThreadCancelResponse,
    )
    async def cancel_execution(
        thread_id: str,
        request: Request,
        body: Annotated[ThreadCancelRequest | None, Body()] = None,
    ) -> dict[str, Any]:
        """Cancela la ejecución en curso del hilo (jobs en vuelo) y lo reabre en ``open``."""
        require_write(request)
        payload = body or ThreadCancelRequest()
        try:
            return coordinator().cancel_execution(
                thread_id=thread_id,
                reason=payload.reason,
                actor=payload.actor,
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
        """Resuelve una decisión y reanuda solo cuando el batch pendiente queda completo.

        Acepta selección múltiple y/o texto libre: el resumen de texto (compatible con los lectores
        existentes, incluido el ProductOwnerAgent) viaja como ``resolution``; la selección
        estructurada queda además en ``decision.metadata.answer`` para quien la necesite completa.
        """
        require_write(request)
        resolution_text = _combined_resolution_text(body)
        if not resolution_text:
            raise HTTPException(status_code=422, detail="resolution, selectedOptions or freeText is required")
        # Only forward the structured answer when the caller actually used it: an explicit-only
        # `resolution` (legacy clients, other callers of resolve_thread_decision) must keep matching
        # as one combined string, not an empty selectedOptions=[] that would look "structured but
        # empty" to _answer_question and reject a perfectly valid plain answer.
        has_structured_answer = bool(body.selected_options) or bool((body.free_text or "").strip())
        try:
            result = BlockerRemediationService(
                platform.connection,
                root=getattr(platform, "cwd", None),
            ).resolve_thread_decision(
                thread_id=thread_id,
                decision_id=decision_id,
                resolution=resolution_text,
                decided_by=body.decided_by,
                platform=platform,
                # Structured, not just the joined-text fallback above: lets _answer_question validate
                # each selected option individually and accept free text for Product Owner decisions
                # even when options are offered, instead of matching the combined string as a whole.
                selected_options=body.selected_options if has_structured_answer else None,
                free_text=body.free_text if has_structured_answer else None,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if body.selected_options or (body.free_text or "").strip():
            updated_decision = ThreadsRepository(platform.connection).update_decision_metadata(
                decision_id,
                {"answer": {"selectedOptions": body.selected_options, "freeText": body.free_text or ""}},
            )
            result = {**result, "decision": updated_decision}
        return result

    @router.post(
        "/api/v1/threads/{thread_id}/decisions/resolve-batch",
        response_model=ThreadDecisionBatchResolveResponse,
    )
    async def resolve_decisions_batch(
        thread_id: str,
        body: ThreadDecisionBatchResolveRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Resuelve varias decisiones juntas y deja un único mensaje de usuario en el chat.

        Cada resolución individual sigue usando su propia transacción corta (mismo camino que el
        endpoint de a una, incluida su remediation real cuando existe) — anidar todo el lote en una
        única transacción SQL chocaría con otros métodos de remediations que abren la suya propia
        sin importar si ya hay una activa. En su lugar, una validación previa (decisión pendiente y
        del hilo correcto, respuesta no vacía) se corre para todo el lote antes de resolver ninguna,
        para que el motivo más común de fallo a mitad de camino nunca llegue a mutar nada.
        """
        require_write(request)
        if not body.answers:
            raise HTTPException(status_code=422, detail="At least one answer is required")
        threads_repo = ThreadsRepository(platform.connection)
        service = BlockerRemediationService(platform.connection, root=getattr(platform, "cwd", None))
        prepared: list[tuple[Any, str]] = []
        for answer in body.answers:
            resolution_text = _combined_answer_text(
                resolution=None, selected_options=answer.selected_options, free_text=answer.free_text
            )
            if not resolution_text:
                raise HTTPException(
                    status_code=422,
                    detail=f"selectedOptions or freeText is required for decision {answer.decision_id}",
                )
            try:
                decision = threads_repo.get_decision(answer.decision_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            if decision["threadId"] != thread_id:
                raise HTTPException(status_code=404, detail=f"Decision not found: {answer.decision_id}")
            if decision["status"] != "pending":
                raise HTTPException(
                    status_code=422,
                    detail=f"Decision {answer.decision_id} is already {decision['status']}",
                )
            prepared.append((answer, resolution_text))

        summary_lines: list[str] = []
        resolved_ids: list[str] = []
        try:
            for answer, resolution_text in prepared:
                decision_before = threads_repo.get_decision(answer.decision_id)
                service.resolve_thread_decision(
                    thread_id=thread_id,
                    decision_id=answer.decision_id,
                    resolution=resolution_text,
                    decided_by=body.decided_by,
                    platform=platform,
                    selected_options=answer.selected_options,
                    free_text=answer.free_text,
                    suppress_message=True,
                )
                if answer.selected_options or (answer.free_text or "").strip():
                    threads_repo.update_decision_metadata(
                        answer.decision_id,
                        {
                            "answer": {
                                "selectedOptions": answer.selected_options,
                                "freeText": answer.free_text or "",
                            }
                        },
                    )
                summary_lines.append(f"{decision_before['title']}: {resolution_text}")
                resolved_ids.append(answer.decision_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        threads_repo.append_message(
            thread_id=thread_id,
            kind="user",
            author=body.decided_by or "user",
            content="\n".join(summary_lines),
            metadata={"decisionIds": resolved_ids},
        )
        decisions = [threads_repo.get_decision(answer.decision_id) for answer, _ in prepared]
        return {"thread": threads_repo.get_thread(thread_id), "decisions": decisions}

    return router
