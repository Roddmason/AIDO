"""Respuestas del operador a las preguntas pendientes de un hilo y su efecto sobre el loop.

Familia extraída de ``remediations/service.py``: cada función recibe el ``BlockerRemediationService``
y valida la respuesta contra la decisión persistida, descarta acciones obsoletas frente al estado del
loop, registra la respuesta (y la de clarificación de discovery) y, en transacción, difiere el lote de
preguntas del ProductOwnerAgent o reemplaza el loop respondido por su continuación. Lo que el servicio
expone o los tests parchean sigue disponible como delegador en la clase.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import nullcontext, suppress
from typing import TYPE_CHECKING, Any

from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.remediations.decision_answers import answer_options, validated_answer
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

if TYPE_CHECKING:
    from local_control_center.remediations.service import BlockerRemediationService

__all__ = [
    "answer_question",
    "answer_question_loop_staleness",
    "defer_answered_decision_batch_in_transaction",
    "record_clarification_answer",
    "remaining_product_owner_decision_ids",
    "supersede_answered_decision_loop_in_transaction",
    "validate_thread_decision_answer",
]


def answer_question(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    payload: dict[str, Any],
    allow_dismissed: bool = False,
) -> dict[str, Any]:
    """Registra la respuesta a la pregunta de la acción y reanuda o difiere el loop según corresponda."""
    action_payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    persisted_thread_id = str(action.get("threadId") or action_payload.get("threadId") or "").strip()
    payload_thread_id = str(payload.get("threadId") or "").strip()
    if persisted_thread_id and payload_thread_id and payload_thread_id != persisted_thread_id:
        return {
            "status": "blocked",
            "action": "answer_question",
            "reason": "threadId does not match the persisted remediation action.",
        }
    persisted_decision_id = str(action_payload.get("decisionId") or "").strip()
    payload_decision_id = str(payload.get("decisionId") or "").strip()
    if persisted_decision_id and payload_decision_id and payload_decision_id != persisted_decision_id:
        return {
            "status": "blocked",
            "action": "answer_question",
            "reason": "decisionId does not match the persisted remediation action.",
        }
    thread_id = persisted_thread_id or payload_thread_id
    decision_id = persisted_decision_id or payload_decision_id
    answer = str(payload.get("answer") or payload.get("resolution") or "").strip()
    raw_selected_options = payload.get("selectedOptions")
    selected_options = (
        [str(item).strip() for item in raw_selected_options if str(item).strip()]
        if isinstance(raw_selected_options, list)
        else []
    )
    free_text = str(payload.get("freeText") or "").strip()
    if "selectedOptions" not in payload and "freeText" not in payload and answer:
        selected_options = [answer]
    if not thread_id or not decision_id or not (answer or selected_options or free_text):
        return {
            "status": "blocked",
            "action": "answer_question",
            "reason": "threadId, decisionId and answer/resolution/selectedOptions/freeText are required.",
        }
    threads = ThreadsRepository(service.connection)
    decision = threads.get_decision(decision_id)
    if decision["threadId"] != thread_id:
        return {
            "status": "blocked",
            "action": "answer_question",
            "reason": f"Decision {decision_id} does not belong to thread {thread_id}.",
        }
    stale_loop_block = answer_question_loop_staleness(service, action=action)
    if stale_loop_block:
        return stale_loop_block
    thread = threads.get_thread(thread_id)
    thread_status = str(thread.get("status") or "").strip()
    if thread_status in {"queued", "running"}:
        return {
            "status": "blocked",
            "action": "answer_question",
            "threadId": thread_id,
            "decisionId": decision_id,
            "threadStatus": thread_status,
            "reason": (
                f"Thread is already {thread_status}; answer_question remediation cannot "
                "safely resolve this decision until the active run finishes."
            ),
        }
    try:
        combined_answer = validated_answer(
            decision=decision,
            action_payload=action_payload,
            selected_options=selected_options,
            free_text=free_text,
        )
    except ValueError as error:
        return {
            "status": "blocked",
            "action": "answer_question",
            "decisionId": decision_id,
            "options": answer_options(decision=decision, action_payload=action_payload),
            "reason": str(error),
        }
    answer = ", ".join(combined_answer)
    suppress_message = bool(payload.get("suppressMessage"))
    from local_control_center.threads.coordinator import ThreadCoordinator

    clarification_payload = {
        **action_payload,
        "answer": answer,
        "resolution": answer,
        "decidedBy": str(payload.get("decidedBy") or "remediation"),
    }
    transaction = (
        nullcontext(service.connection)
        if service.connection.in_transaction
        else immediate_transaction(service.connection)
    )
    with transaction:
        current_action = service.repository.get(action["id"])
        executable_statuses = {"pending", "dismissed"} if allow_dismissed else {"pending"}
        if current_action["status"] not in executable_statuses:
            return {
                "status": "blocked",
                "action": "answer_question",
                "remediationStatus": current_action["status"],
                "reason": "Only pending remediation actions can be executed.",
            }
        stale_loop_block = answer_question_loop_staleness(service, action=current_action)
        if stale_loop_block:
            return stale_loop_block
        current_thread = threads.get_thread(thread_id)
        current_thread_status = str(current_thread.get("status") or "").strip()
        if current_thread_status in {"queued", "running"}:
            return {
                "status": "blocked",
                "action": "answer_question",
                "threadId": thread_id,
                "decisionId": decision_id,
                "threadStatus": current_thread_status,
                "reason": (
                    f"Thread is already {current_thread_status}; answer_question remediation "
                    "cannot safely resolve this decision until the active run finishes."
                ),
            }
        remaining_decision_ids = service._remaining_product_owner_decision_ids(
            action=current_action,
            current_decision_id=decision_id,
            threads=threads,
        )
        result = ThreadCoordinator(service.connection, root=service.root).resolve_decision_in_transaction(
            thread_id=thread_id,
            decision_id=decision_id,
            resolution=answer,
            decided_by=str(payload.get("decidedBy") or "remediation"),
            defer_followup=bool(remaining_decision_ids),
            append_message=not suppress_message,
        )
        clarification = service._record_clarification_answer(
            decision=result.get("decision") or {},
            payload=clarification_payload,
            answer=answer,
        )
        if remaining_decision_ids:
            loop_supersede = defer_answered_decision_batch_in_transaction(
                service,
                action=current_action,
                decision_id=decision_id,
                remaining_decision_ids=remaining_decision_ids,
                threads=threads,
            )
        else:
            loop_supersede = supersede_answered_decision_loop_in_transaction(
                service,
                action=current_action,
                result=result,
                answer=answer,
                decision_id=decision_id,
            )
        if service.repository.get(current_action["id"])["status"] != "resolved":
            service.repository.mark_status_in_transaction(current_action["id"], "resolved")
    return {
        "status": "completed",
        "action": "answer_question",
        **result,
        **clarification,
        **loop_supersede,
    }


def answer_question_loop_staleness(
    service: BlockerRemediationService, *, action: dict[str, Any]
) -> dict[str, Any] | None:
    """Bloqueo si el loop de la acción ya no espera esta pregunta; ``None`` si sigue vigente."""
    loop_id = str(action.get("loopId") or "").strip()
    if not loop_id:
        return None
    try:
        loop = service._coordinator().get(loop_id)
    except KeyError:
        return None
    durable = dict((loop.get("context") or {}).get("durableRun") or {})
    blocker_type = str(action.get("blockerType") or "").strip()
    if blocker_type == "po_needs_input" and loop["state"] != "awaiting_user":
        return {
            "status": "blocked",
            "action": "answer_question",
            "reason": "Product Loop is not awaiting this ProductOwnerAgent question.",
            "loopId": loop_id,
            "loopState": loop["state"],
            "blockedStage": durable.get("blockedStage"),
        }
    if blocker_type == "functionality_memory_decision_required" and (
        loop["state"] != "blocked" or durable.get("blockedStage") != "functionality_memory"
    ):
        return {
            "status": "blocked",
            "action": "answer_question",
            "reason": "Product Loop is no longer blocked on this functionality decision.",
            "loopId": loop_id,
            "loopState": loop["state"],
            "blockedStage": durable.get("blockedStage"),
        }
    return None


def remaining_product_owner_decision_ids(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    current_decision_id: str,
    threads: ThreadsRepository,
) -> list[str]:
    """Ids de las demás decisiones del ProductOwnerAgent que siguen pendientes en el mismo lote."""
    if action.get("blockerType") != "po_needs_input":
        return []
    loop_id = str(action.get("loopId") or "").strip()
    thread_id = str(action.get("threadId") or "").strip()
    if not loop_id or not thread_id:
        return []
    loop = service._coordinator().get(loop_id)
    durable = dict((loop.get("context") or {}).get("durableRun") or {})
    product_owner = durable.get("productOwner") if isinstance(durable.get("productOwner"), dict) else {}
    pending_specs = product_owner.get("pendingThreadDecisions")
    candidate_ids = [
        str(item.get("decisionId") or "").strip()
        for item in pending_specs or []
        if isinstance(item, dict) and str(item.get("decisionId") or "").strip()
    ]
    if not candidate_ids:
        candidate_ids = [
            str((item.get("payload") or {}).get("decisionId") or "").strip()
            for item in service.repository.list_for_thread(thread_id)
            if item.get("loopId") == loop_id
            and item.get("blockerType") == "po_needs_input"
            and item.get("actionType") == "answer_question"
        ]
    remaining: list[str] = []
    for candidate_id in dict.fromkeys(candidate_ids):
        if not candidate_id or candidate_id == current_decision_id:
            continue
        with suppress(KeyError):
            decision = threads.get_decision(candidate_id)
            if decision["threadId"] == thread_id and decision["status"] == "pending":
                remaining.append(candidate_id)
    return remaining


def defer_answered_decision_batch_in_transaction(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    decision_id: str,
    remaining_decision_ids: list[str],
    threads: ThreadsRepository,
) -> dict[str, Any]:
    """Guarda la respuesta en el lote pendiente del ProductOwnerAgent sin reanudar el loop todavía."""
    if not service.connection.in_transaction:
        raise RuntimeError("_defer_answered_decision_batch_in_transaction requires an active transaction.")
    loop_id = str(action.get("loopId") or "").strip()
    thread_id = str(action.get("threadId") or "").strip()
    coordinator = service._coordinator()
    loop = coordinator.get(loop_id)
    durable = dict((loop.get("context") or {}).get("durableRun") or {})
    product_owner = durable.get("productOwner") if isinstance(durable.get("productOwner"), dict) else {}
    remaining_set = set(remaining_decision_ids)
    pending_specs = [
        item
        for item in product_owner.get("pendingThreadDecisions") or []
        if isinstance(item, dict) and str(item.get("decisionId") or "") in remaining_set
    ]
    answered = [
        item
        for item in product_owner.get("answeredThreadDecisions") or []
        if isinstance(item, dict) and item.get("decisionId") != decision_id
    ]
    answered_at = utc_now()
    answered.append(
        {
            "decisionId": decision_id,
            "remediationActionId": action["id"],
            "answeredAt": answered_at,
        }
    )
    updated_loop = coordinator.repository.update_loop_context(
        loop_id,
        context={
            **loop["context"],
            "durableRun": {
                **durable,
                "status": "awaiting_user",
                "productOwner": {
                    **product_owner,
                    "status": "needs_input",
                    "pendingThreadDecisions": pending_specs,
                    "answeredThreadDecisions": answered,
                },
                "updatedAt": answered_at,
            },
        },
    )
    threads.record_event(
        thread_id=thread_id,
        type="decision_batch_progress",
        agent_role="product_owner",
        payload={
            "loopId": loop_id,
            "decisionId": decision_id,
            "remainingDecisionIds": remaining_decision_ids,
            "remainingDecisionCount": len(remaining_decision_ids),
            "remediationActionId": action["id"],
        },
    )
    service.repository.mark_status_in_transaction(action["id"], "resolved")
    return {
        "loop": updated_loop,
        "loopSupersede": {
            "status": "deferred",
            "decisionId": decision_id,
            "remainingDecisionIds": remaining_decision_ids,
            "remainingDecisionCount": len(remaining_decision_ids),
        },
    }


def supersede_answered_decision_loop_in_transaction(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    result: dict[str, Any],
    answer: str,
    decision_id: str,
) -> dict[str, Any]:
    """Reemplaza el loop respondido por la continuación encolada, dentro de la transacción del caller."""
    if not service.connection.in_transaction:
        raise RuntimeError("_supersede_answered_decision_loop_in_transaction requires an active transaction.")
    loop_id = str(action.get("loopId") or "").strip()
    queued_job = result.get("job") if isinstance(result.get("job"), dict) else None
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
    thread_id = str(action.get("threadId") or thread.get("id") or "").strip()
    if not loop_id or queued_job is None:
        return {}

    from local_control_center.product_loop.coordinator import allowed_next_states

    coordinator = service._coordinator()
    loop = coordinator.get(loop_id)
    if loop["state"] == "cancelled":
        return {"loop": loop, "loopSupersede": {"status": "already_cancelled"}}
    if "cancelled" not in allowed_next_states(loop["state"]):
        return {
            "loop": loop,
            "loopSupersede": {
                "status": "skipped",
                "reason": f"Product Loop state {loop['state']} cannot be superseded.",
            },
        }
    queued_at = utc_now()
    durable = dict((loop.get("context") or {}).get("durableRun") or {})
    decision_answer = {
        "status": "queued",
        "jobId": queued_job["id"],
        "threadId": thread_id,
        "decisionId": decision_id,
        "answer": answer,
        "remediationActionId": action["id"],
        "queuedAt": queued_at,
    }
    durable = {
        **durable,
        "status": "decision_answer_queued",
        "decisionAnswer": decision_answer,
        "updatedAt": queued_at,
    }
    superseded = coordinator.transition_in_transaction(
        loop_id,
        to_state="cancelled",
        reason="Product Loop awaiting a decision was superseded by a queued answered-decision job.",
        actor="remediation",
        trigger="answer_question",
        context_patch={"durableRun": durable},
        metadata={
            "remediationActionId": action["id"],
            "decisionId": decision_id,
            "queuedJobId": queued_job["id"],
        },
        expected_version=loop["version"],
    )
    return {
        "loop": superseded,
        "loopSupersede": {
            "status": "cancelled",
            "jobId": queued_job["id"],
            "threadId": thread_id,
            "decisionId": decision_id,
        },
    }


def validate_thread_decision_answer(
    service: BlockerRemediationService,
    *,
    thread_id: str,
    decision_id: str,
    selected_options: list[str],
    free_text: str | None,
) -> None:
    """Comprueba, sin mutar nada, que ``resolve_thread_decision`` aceptaría esta respuesta.

    Replica cada rechazo de la resolución real (decisión del hilo y pendiente, remediación
    vigente, opciones ofrecidas, exclusividad, texto libre, hilo sin run activo). El lote la
    corre para todas sus respuestas antes de resolver la primera: resolver es irreversible y un
    rechazo a mitad de camino dejaba decisiones resueltas sin su mensaje en el chat.

    Raises:
        KeyError: la decisión no existe o no es de ese hilo.
        ValueError: la respuesta o el estado actual impedirían resolverla.
    """
    threads = ThreadsRepository(service.connection)
    decision = threads.get_decision(decision_id)
    if decision["threadId"] != thread_id:
        raise KeyError(f"Decision not found: {decision_id}")
    if decision["status"] != "pending":
        raise ValueError(f"Decision {decision_id} is already {decision['status']}.")
    thread = threads.get_thread(thread_id)
    thread_status = str(thread.get("status") or "").strip()
    if thread_status in {"queued", "running"}:
        raise ValueError(f"Thread is already {thread_status}; wait until the active run finishes.")
    matching_actions = [
        action
        for action in service.list_for_thread(thread_id=thread_id)
        if action.get("actionType") == "answer_question"
        and str((action.get("payload") or {}).get("decisionId") or "").strip() == decision_id
    ]
    usable = [action for action in matching_actions if action.get("status") in {"pending", "dismissed"}]
    if matching_actions and not usable:
        raise ValueError("Decision remediation is no longer pending.")
    if not matching_actions and service._decision_requires_product_loop_remediation(
        thread_id=thread_id, project_id=thread["projectId"], decision_id=decision_id
    ):
        raise ValueError(
            "Product Loop decision remediation is unavailable; resolution is blocked to avoid "
            "starting an incomplete decision batch."
        )
    action_payload = usable[-1].get("payload") if usable else {}
    validated_answer(
        decision=decision,
        action_payload=action_payload if isinstance(action_payload, dict) else {},
        selected_options=[str(item).strip() for item in selected_options if str(item).strip()],
        free_text=(free_text or "").strip(),
    )


def record_clarification_answer(
    service: BlockerRemediationService,
    *,
    decision: dict[str, Any],
    payload: dict[str, Any],
    answer: str,
) -> dict[str, Any]:
    """Registra la respuesta en la pregunta de clarificación de discovery enlazada; ``{}`` si no hay."""
    metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
    question_id = str(
        payload.get("clarificationQuestionId") or metadata.get("clarificationQuestionId") or ""
    ).strip()
    if not question_id:
        return {}
    discovery = ProductDiscoveryRepository(service.connection)
    try:
        question = discovery.get_clarification_question(question_id)
    except KeyError:
        return {
            "clarificationAnswer": {
                "status": "skipped",
                "reason": f"Clarification question not found: {question_id}",
            }
        }
    existing_answer = next(
        (
            item
            for item in discovery.list_clarification_answers(question_id)
            if item["status"] == "accepted" and item["answer"] == answer
        ),
        None,
    )
    if existing_answer is None:
        existing_answer = discovery.create_clarification_answer(
            {
                "projectId": question["projectId"],
                "questionId": question_id,
                "initiativeId": question.get("initiativeId") or payload.get("initiativeId") or "",
                "answer": answer,
                "status": "accepted",
                "answeredBy": str(payload.get("decidedBy") or "remediation"),
                "metadata": {
                    "source": "blocker_remediation",
                    "threadDecisionId": decision.get("id"),
                    "options": metadata.get("options") or payload.get("options") or [],
                },
            }
        )
    question_metadata = question.get("metadata") if isinstance(question.get("metadata"), dict) else {}
    updated_question = discovery.update_clarification_question(
        question_id,
        {
            "status": "answered",
            "metadata": {
                **question_metadata,
                "remediationAnswer": answer,
                "remediationAnsweredAt": utc_now(),
                "remediationDecisionId": decision.get("id"),
            },
        },
    )
    return {"clarificationAnswer": existing_answer, "clarificationQuestion": updated_question}
