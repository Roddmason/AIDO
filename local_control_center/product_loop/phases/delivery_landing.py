"""Cierre de la entrega: aprobación, aterrizaje del trabajo y continuaciones por feedback.

Fase del pipeline extraída de ``coordinator.py``: cada función recibe el coordinator y opera sobre la
aprobación de entrega del loop —resolverla y registrar la decisión—, encola y ejecuta el aterrizaje
del trabajo entregado en la rama base, informa su resultado al hilo y, ante feedback sobre una entrega,
encola la continuación con su pedido de cambio y sincroniza el estado del hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
from local_control_center.product_loop.delivery import (
    DELIVERY_LANDING_JOB_KIND,
    LANDING_QUEUED,
    LANDING_WAITING_CAPACITY,
    ProductLoopDeliveryService,
)
from local_control_center.product_loop.fsm import DELIVERED_STATE, ProductLoopTransitionError
from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
from local_control_center.product_loop.run_instructions import CHANGE_REQUEST_HEADER, continuation_run_message
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

__all__ = [
    "close_accepted_delivery_approval_job",
    "continuation_root",
    "delivery_approval_ref",
    "delivery_change_request",
    "delivery_thread_id",
    "dismiss_open_product_owner_decisions",
    "land_delivered_work",
    "land_queued_delivery",
    "landing_root",
    "queue_delivery_feedback_continuation",
    "queue_delivery_landing",
    "queue_root",
    "record_delivery_approval_decision",
    "report_delivery_landing",
    "resolve_delivery_approval_action",
    "source_thread_message",
    "sync_delivery_feedback_thread_state",
]


def delivery_approval_ref(coordinator: ProductLoopCoordinator, loop: dict[str, Any]) -> dict[str, str] | None:
    """Ids de job y action request de la aprobación de entrega del ``durableRun``; ``None`` si falta alguno."""
    approval = coordinator._durable_run_context(loop).get("approval")
    if not isinstance(approval, dict):
        return None
    job_id = str(approval.get("jobId") or "").strip()
    action_id = str(approval.get("actionRequestId") or "").strip()
    if not job_id or not action_id:
        return None
    return {"jobId": job_id, "actionRequestId": action_id}


def resolve_delivery_approval_action(
    coordinator: ProductLoopCoordinator,
    *,
    loop: dict[str, Any],
    decision: str,
    reason: str,
    actor: str,
) -> dict[str, Any] | None:
    """Resuelve la action request de la aprobación de entrega según la decisión; ``None`` si no aplica."""
    ref = delivery_approval_ref(coordinator, loop)
    if ref is None or decision not in {"accept", "request_changes"}:
        return None
    job_id = ref["jobId"]
    action_id = ref["actionRequestId"]
    try:
        action = coordinator.jobs.get_action_request(action_id)
    except KeyError as error:
        raise ProductLoopTransitionError(f"Delivery approval action not found: {action_id}.") from error
    if action["jobId"] != job_id:
        raise ProductLoopTransitionError(
            f"Delivery approval action {action_id} is not scoped to job {job_id}."
        )
    if action["projectId"] != loop["projectId"]:
        raise ProductLoopTransitionError(
            f"Delivery approval action {action_id} is not scoped to product loop project {loop['projectId']}."
        )
    if action["actionType"] not in {
        "product_loop.approve_delivery",
        "job.product_loop_delivery_approval",
    }:
        raise ProductLoopTransitionError(
            f"Delivery approval action {action_id} has unexpected type {action['actionType']}."
        )
    if action["status"] == "pending" and decision == "accept":
        action = coordinator.jobs.approve_action(job_id, action_id, reason=reason, actor=actor)[
            "actionRequest"
        ]
        close_accepted_delivery_approval_job(coordinator, job_id)
    elif action["status"] == "pending" and decision == "request_changes":
        action = coordinator.jobs.deny_action(job_id, action_id, reason=reason, actor=actor)["actionRequest"]
    return {
        "type": "resolve_delivery_approval_action",
        "decision": decision,
        "jobId": job_id,
        "actionRequestId": action_id,
        "status": action["status"],
        "decidedBy": action.get("decidedBy"),
        "decidedAt": action.get("decidedAt"),
    }


def close_accepted_delivery_approval_job(coordinator: ProductLoopCoordinator, job_id: str) -> None:
    """Cierra el job de aprobación de entrega aceptada: solo contenía la decisión.

    ``approve_action`` deja ``queued`` el job de su última acción para que se ejecute, pero el
    loop ya aplicó la entrega y el worker no tiene nada que correr: lo fallaba con "Unsupported
    job kind" justo después de una aprobación válida (visto en vivo). Corre en la transacción de
    ``apply_feedback``, así el worker nunca ve el estado intermedio.
    """
    job = coordinator.jobs.get_job(job_id)
    if job["kind"] != "product_loop_delivery_approval" or job["status"] != "queued":
        return
    coordinator.jobs.update_job_status(
        job_id,
        status="completed",
        metadata={
            "status": "completed",
            "decision": "accept",
            "summary": "Delivery accepted; the Product Loop applies it, nothing else runs for this job.",
        },
    )


def record_delivery_approval_decision(
    coordinator: ProductLoopCoordinator, loop: dict[str, Any], effect: dict[str, Any] | None
) -> dict[str, Any]:
    """Guarda en el ``durableRun`` del loop la decisión aplicada a su aprobación de entrega."""
    if effect is None:
        return loop
    durable = coordinator._durable_run_context(loop)
    approval = dict(durable.get("approval") or {})
    approval.update(
        {
            "status": effect["status"],
            "decision": effect["decision"],
            "jobId": effect["jobId"],
            "actionRequestId": effect["actionRequestId"],
            "decidedBy": effect.get("decidedBy"),
            "decidedAt": effect.get("decidedAt"),
        }
    )
    durable["approval"] = approval
    durable["updatedAt"] = utc_now()
    return coordinator._write_context_entries(loop, {"durableRun": durable})


def landing_root(coordinator: ProductLoopCoordinator, project_id: str) -> Path:
    """Raíz donde aterriza la entrega: la del coordinator o, en su defecto, la del proyecto."""
    return (
        coordinator.root if coordinator.root is not None else Path(queue_root(coordinator, project_id) or ".")
    )


def queue_delivery_landing(
    coordinator: ProductLoopCoordinator, loop: dict[str, Any], *, feedback_id: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Encola el aterrizaje de la entrega recién aprobada en un job ``product_loop.land_delivery``.

    Aterrizar en la petición de aprobación la dejaba colgada ~23 s (cada git pasa por el broker y
    el supervisor; visto en el spec del ciclo de vida) y, sin lease padre, cada git pedía su propia
    reserva: con el host justo el gobernador lo negaba (visto en vivo). En el worker, el lease del
    job cubre a sus git hijos y la admisión espera capacidad. Jamás revierte la entrega aprobada.
    """
    durable = coordinator._durable_run_context(loop)
    if not str(durable.get("workspaceId") or "").strip():
        return loop, None
    job = coordinator.jobs.create_job(
        project_id=str(loop["projectId"]),
        kind=DELIVERY_LANDING_JOB_KIND,
        payload={"loopId": loop["id"], "root": str(landing_root(coordinator, str(loop["projectId"])))},
        idempotency_key=f"product-loop-landing:{loop['id']}",
    )["job"]
    durable["landing"] = {"status": LANDING_QUEUED, "jobId": job["id"], "feedbackId": feedback_id}
    durable["updatedAt"] = utc_now()
    loop = coordinator._write_context_entries(loop, {"durableRun": durable})
    return loop, {"type": "delivery_landing", "status": LANDING_QUEUED, "jobId": job["id"]}


def land_delivered_work(coordinator: ProductLoopCoordinator, loop: dict[str, Any]) -> dict[str, Any]:
    """Aterriza la rama de trabajo del loop entregado según ``project.git.integrationMode``.

    Best-effort: un aterrizaje bloqueado o fallido se persiste como evidencia en el contexto
    durable (``landing``), pero jamás revierte la entrega ya aprobada.
    """
    durable = coordinator._durable_run_context(loop)
    project_id = str(loop["projectId"])
    try:
        landing = ProductLoopDeliveryService(
            coordinator.connection, root=landing_root(coordinator, project_id)
        ).land(
            project_id=project_id,
            workspace_id=str(durable.get("workspaceId") or "").strip(),
            loop_id=str(loop["id"]),
            title=str(loop.get("title") or ""),
            loop=loop,
        )
    except Exception as error:
        landing = {"status": "landing_failed", "reason": redact_secrets(str(error))}
    queued = durable.get("landing") or {}
    durable["landing"] = redact_secrets(
        {**landing, "jobId": queued.get("jobId"), "feedbackId": queued.get("feedbackId")}
    )
    durable["updatedAt"] = utc_now()
    coordinator._write_context_entries(loop, {"durableRun": durable})
    return durable["landing"]


def land_queued_delivery(coordinator: ProductLoopCoordinator, loop_id: str) -> dict[str, Any]:
    """Aterriza una entrega aprobada desde su job; un aterrizaje ya resuelto no se repite.

    Reintentar el job fallido (p. ej. tras resolver un conflicto o limpiar la base) vuelve a
    intentar un aterrizaje bloqueado o fallido: nunca uno que ya aterrizó o espera un PR.
    """
    loop = coordinator.get(loop_id)
    landing = coordinator._durable_run_context(loop).get("landing") or {}
    if loop["state"] != DELIVERED_STATE or landing.get("status") not in {
        LANDING_QUEUED,
        LANDING_WAITING_CAPACITY,
        "landing_blocked",
        "landing_failed",
    }:
        return landing
    landing = coordinator._land_delivered_work(loop)
    coordinator._report_delivery_landing(loop, landing)
    return landing


def report_delivery_landing(
    coordinator: ProductLoopCoordinator, loop: dict[str, Any], landing: dict[str, Any]
) -> None:
    """Deja el resultado del aterrizaje donde mira el operador: el hilo y el feedback de la aprobación.

    El aterrizaje corre en un job cuando el hilo ya figura entregado: sin esto un aterrizaje fallido
    no dejaba ninguna señal (hallazgo de la revisión independiente) y el efecto del feedback quedaba
    en "queued" para siempre.
    """
    status = str(landing.get("status") or "unknown")
    step = landing.get("merge") or landing.get("push") or {}
    reason = str(step.get("reason") or landing.get("reason") or step.get("status") or "")
    if status == "landed":
        event_type = "delivery_landed"
    elif status in {LANDING_WAITING_CAPACITY, "landing_blocked", "landing_failed"}:
        event_type = "delivery_landing_failed"
    else:
        event_type = "delivery_landing"
    coordinator._record_thread_event(
        thread_id=delivery_thread_id(coordinator, loop),
        event_type=event_type,
        payload=redact_secrets(
            {
                "loopId": loop["id"],
                "status": status,
                "reason": reason,
                "jobId": landing.get("jobId"),
                "baseBranch": landing.get("baseBranch"),
            }
        ),
        agent_role="aido_lead",
    )
    feedback_id = str(landing.get("feedbackId") or "").strip()
    if not feedback_id:
        return
    with suppress(KeyError):
        feedback = coordinator.repository.get_feedback(feedback_id)
        effects = [
            {**effect, "status": status} if effect.get("type") == "delivery_landing" else effect
            for effect in feedback.get("effects") or []
        ]
        coordinator.repository.update_feedback_effects(
            feedback_id, effects=effects, status=feedback["status"]
        )


def delivery_thread_id(coordinator: ProductLoopCoordinator, loop: dict[str, Any]) -> str:
    """Id del hilo del proyecto asociado al loop; vacío si no hay."""
    thread = coordinator._durable_run_context(loop).get("thread")
    if not isinstance(thread, dict):
        return ""
    return str(thread.get("projectThreadId") or "").strip()


def queue_root(coordinator: ProductLoopCoordinator, project_id: str) -> str | None:
    """Raíz absoluta del repositorio (la del coordinator o la del proyecto); ``None`` si no se conoce."""
    if coordinator.root is not None:
        return str(coordinator.root)
    try:
        project = ProjectsRepository(coordinator.connection).get_project(project_id)
    except KeyError:
        return None
    path = str(project.get("path") or "").strip()
    if not path:
        return None
    return str(Path(path).resolve(strict=False))


def source_thread_message(
    *,
    threads: ThreadsRepository,
    thread_id: str,
    message_id: str,
) -> dict[str, Any] | None:
    """Mensaje de usuario que originó la entrega: el indicado o, si no, el último del hilo."""
    if message_id:
        return threads.get_message(message_id)
    user_messages = [message for message in threads.list_messages(thread_id) if message["kind"] == "user"]
    return user_messages[-1] if user_messages else None


def continuation_root(coordinator: ProductLoopCoordinator, request_meta: dict[str, Any]) -> str | None:
    """Raíz del control plane con la que corrió el run que se continúa.

    La continuación debe escribir su evidencia donde la escribió el run original. Sin raíz propia
    (API de feedback, deny de la entrega) caía a la ruta del proyecto: el run dejaba
    ``.tmp/evidence-artifacts`` dentro del repo del usuario y el siguiente run se bloqueaba por
    árbol git sucio (visto en vivo en el flujo de punto a punto).
    """
    if coordinator.root is not None:
        return str(coordinator.root)
    job_id = str(request_meta.get("jobId") or "").strip()
    if not job_id:
        return None
    try:
        payload = coordinator.jobs.get_job(job_id).get("payload") or {}
    except KeyError:
        return None
    root = str(payload.get("root") or "").strip() if isinstance(payload, dict) else ""
    return root or None


def delivery_change_request(coordinator: ProductLoopCoordinator, loop_id: str, feedback: str) -> str:
    """Bloque con lo que el operador pidió cambiar, para que el PO y el Developer lo lean.

    El run de continuación repite el mensaje original del hilo; sin este bloque el pedido de
    cambios (el último ``request_changes`` y el texto del ``continue``) solo quedaba en metadatos
    que ningún agente lee, y el rework rehacía exactamente lo mismo que se había rechazado.
    """
    notes = [
        str(item.get("feedback") or "").strip()
        for item in coordinator.repository.list_feedback(loop_id=loop_id)
        if item.get("action") == "request_changes"
    ][-1:]
    notes.append(str(feedback or "").strip())
    unique = [note for note in dict.fromkeys(notes) if note]
    if not unique:
        return ""
    return f"{CHANGE_REQUEST_HEADER}\n" + "\n".join(unique)


def queue_delivery_feedback_continuation(
    coordinator: ProductLoopCoordinator,
    *,
    loop: dict[str, Any],
    feedback_id: str,
    feedback: str,
    actor: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Encola la continuación del loop entregado con el pedido de cambio del feedback."""
    durable = coordinator._durable_run_context(loop)
    thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
    request_meta = (
        strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta"))
        if isinstance(durable.get("requestMeta"), dict)
        else {}
    )
    thread_id = str(thread_ref.get("projectThreadId") or "").strip()
    if not thread_id:
        raise ProductLoopTransitionError("Feedback action continue requires a Product Loop thread.")

    threads = ThreadsRepository(coordinator.connection)
    thread = threads.get_thread(thread_id)
    if thread["projectId"] != loop["projectId"]:
        raise ProductLoopTransitionError("Thread project does not match the Product Loop project.")
    if thread["status"] in {"queued", "running"}:
        raise ProductLoopTransitionError(
            f"Thread is already {thread['status']}; continue would create concurrent execution."
        )

    message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
    message = str(durable.get("message") or "").strip()
    source_message = source_thread_message(
        threads=threads,
        thread_id=thread_id,
        message_id=message_id,
    )
    if source_message is not None:
        message_id = source_message["id"]
        message = message or str(source_message["content"] or "").strip()
    if not message_id or not message:
        raise ProductLoopTransitionError("Feedback action continue requires the original thread message.")

    root = continuation_root(coordinator, request_meta)
    if not root:
        raise ProductLoopTransitionError(
            "The control-plane root of the continued run is required to queue its continuation."
        )

    run_message = continuation_run_message(
        message, delivery_change_request(coordinator, loop["id"], feedback)
    )
    queued_at = utc_now()
    plan_only = bool(request_meta.get("planOnly") or request_meta.get("plan_only"))
    approved_resource_selections = coordinator._approved_resource_selections_from_durable(durable)
    run_metadata = {
        **request_meta,
        "messageId": message_id,
        "planOnly": plan_only,
        "approvedResourceSelections": approved_resource_selections,
        "continueOfLoopId": loop["id"],
        "continueReason": feedback,
        "feedbackId": feedback_id,
        "continueQueuedAt": queued_at,
    }
    job = coordinator.jobs.create_job(
        project_id=loop["projectId"],
        kind="thread.product_loop.run",
        payload={
            "threadId": thread_id,
            "messageId": message_id,
            "projectId": loop["projectId"],
            "message": run_message,
            "title": thread["title"] or loop["title"],
            "root": root,
            "decision": request_meta.get("decision"),
            "teamPlan": request_meta.get("teamPlan"),
            "planOnly": plan_only,
            "approvedResourceSelections": approved_resource_selections,
            "runMetadata": run_metadata,
            "continueOfLoopId": loop["id"],
            "continueReason": feedback,
            "feedbackId": feedback_id,
            "continueQueuedAt": queued_at,
        },
        idempotency_key=f"product-loop-continue:{loop['id']}:{feedback_id}",
    )["job"]
    continuation = {
        "status": "queued",
        "jobId": job["id"],
        "threadId": thread_id,
        "messageId": message_id,
        "feedbackId": feedback_id,
        "queuedAt": queued_at,
    }
    durable = {
        **durable,
        "status": "continuation_queued",
        "continuation": continuation,
        "updatedAt": queued_at,
    }
    loop = coordinator._write_context_entries(loop, {"durableRun": durable})
    threads.set_status(thread_id, "queued")
    threads.record_event(
        thread_id=thread_id,
        type="run_queued",
        agent_role=actor,
        payload={
            "loopId": loop["id"],
            "jobId": job["id"],
            "messageId": message_id,
            "status": job["status"],
            "continueOfLoopId": loop["id"],
            "feedbackId": feedback_id,
        },
    )
    return loop, {
        "type": "queue_continuation",
        "jobId": job["id"],
        "threadId": thread_id,
        "messageId": message_id,
        "status": job["status"],
    }


def sync_delivery_feedback_thread_state(
    coordinator: ProductLoopCoordinator,
    *,
    loop: dict[str, Any],
    decision: str,
    reason: str,
    actor: str,
) -> dict[str, Any] | None:
    """Sincroniza el estado del hilo y sus decisiones abiertas con el feedback sobre la entrega."""
    thread_id = delivery_thread_id(coordinator, loop)
    if not thread_id:
        return None
    dismissed: list[str] = []
    if decision == "accept":
        status = "resolved"
        event_type = "completed"
        thread_ref = coordinator._durable_run_context(loop).get("thread")
        dismissed = dismiss_open_product_owner_decisions(
            coordinator,
            thread_id,
            source_message_id=str((thread_ref or {}).get("messageId") or "")
            if isinstance(thread_ref, dict)
            else "",
        )
    elif decision == "request_changes":
        status = "open"
        event_type = "reworking"
    else:
        return None
    coordinator._set_thread_status_best_effort(
        thread_id=thread_id,
        status=status,
        reason=f"Delivery feedback {decision}: {reason}",
    )
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type=event_type,
        payload={
            "loopId": loop["id"],
            "decision": decision,
            "status": status,
            "reason": reason,
        },
        agent_role=actor,
    )
    return {
        "type": "sync_delivery_feedback_thread_state",
        "threadId": thread_id,
        "decision": decision,
        "status": status,
        **({"dismissedDecisionIds": dismissed} if dismissed else {}),
    }


def dismiss_open_product_owner_decisions(
    coordinator: ProductLoopCoordinator, thread_id: str, *, source_message_id: str
) -> list[str]:
    """Descarta las preguntas del PO que siguen pendientes del pedido cuya entrega se aceptó.

    Visto en vivo: una pregunta de un run anterior seguía en la barra de ejecución del hilo ya
    entregado; responderla habría reabierto trabajo sobre una entrega aceptada. Solo las del mismo
    mensaje de origen (reintentos y continuaciones lo conservan): la pregunta vigente de otro
    pedido del hilo no se toca. También cierra la pregunta o decisión de producto enlazada, para
    que el PO no la vuelva a pedir.
    """
    if not source_message_id:
        return []
    threads = ThreadsRepository(coordinator.connection)
    dismissed: list[str] = []
    for item in threads.list_decisions(thread_id):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if (
            item["status"] != "pending"
            or metadata.get("source") != PRODUCT_OWNER_AGENT_ID
            or str(metadata.get("sourceMessageId") or "") != source_message_id
        ):
            continue
        threads.resolve_decision(
            thread_id=thread_id,
            decision_id=item["id"],
            resolution="Superseded: the delivery was accepted without this answer.",
            decided_by="aido_lead",
            status="dismissed",
        )
        question_id = str(metadata.get("clarificationQuestionId") or "").strip()
        if question_id:
            with suppress(KeyError):
                coordinator.discovery.update_clarification_question(question_id, {"status": "dismissed"})
        product_decision_id = str(metadata.get("productDecisionId") or "").strip()
        if product_decision_id:
            with suppress(KeyError):
                coordinator.discovery.update_product_decision(product_decision_id, {"status": "superseded"})
        dismissed.append(item["id"])
    return dismissed
