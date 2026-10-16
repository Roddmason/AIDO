"""Persistencia de la salida del ProductOwnerAgent: brief, iniciativa, preguntas, decisiones y backlog.

Fase del pipeline extraída de ``coordinator.py``: cada función recibe el coordinator y valida la salida
del Product Owner, decide su estado de flujo y la materializa de forma idempotente en discovery y en
el hilo —reutilizando lo que el runner ya persistió— para que un replay o un reintento no duplique
registros.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from local_control_center.agents.product_owner_agent import (
    ProductOwnerAgent,
    ProductOwnerOutputValidationError,
    persist_product_owner_backlog,
)
from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

__all__ = [
    "brief_payload",
    "decision_options_with_recommendation_fallback",
    "ensure_product_initiative",
    "ensure_product_owner_thread_decision",
    "has_actionable_product_owner_input",
    "pending_product_owner_thread_decisions",
    "persist_backlog_from_output",
    "persist_clarification_questions",
    "persist_product_brief",
    "persist_product_decisions",
    "persist_product_owner_output_record",
    "product_owner_flow_status",
    "product_owner_output",
    "question_options",
    "reuse_persisted",
    "runner_persisted",
    "stamp_initiative_thread",
]


def product_owner_output(result: dict[str, Any]) -> dict[str, Any]:
    """Extrae y valida el objeto ``output`` del resultado del ProductOwnerAgent."""
    if not isinstance(result, dict):
        raise ProductOwnerOutputValidationError("ProductOwnerAgent result must be a JSON object.")
    raw_output = result.get("output")
    if not isinstance(raw_output, dict):
        reason = str(result.get("reason") or "").strip()
        if reason.startswith("ProductOwnerAgent"):
            raise ProductOwnerOutputValidationError(reason)
        suffix = f": {reason}" if reason else "."
        raise ProductOwnerOutputValidationError(f"ProductOwnerAgent output must be a JSON object{suffix}")
    output = dict(raw_output)
    for key in (
        "status",
        "summary",
        "confidence",
        "questions",
        "assumptions",
        "decisions",
        "productBriefPatch",
        "epics",
        "userStories",
        "risks",
        "recommendedNextAction",
    ):
        if key not in output and key in result:
            output[key] = result[key]
    if "productBriefPatch" not in output:
        output["productBriefPatch"] = result.get("brief") or {}
    validated = ProductOwnerAgent().validate_output(output)
    for key, value in output.items():
        validated.setdefault(key, value)
    return validated


def has_actionable_product_owner_input(
    *,
    questions: list[Any],
    decisions: list[Any],
) -> bool:
    """Indica si alguna pregunta o decisión ofrece al menos dos opciones para que el operador elija."""
    for item in [*questions, *decisions]:
        if not isinstance(item, dict):
            continue
        if len(question_options(item)) >= 2:
            return True
    return False


def product_owner_flow_status(
    coordinator: ProductLoopCoordinator, result: dict[str, Any], output: dict[str, Any] | None = None
) -> str:
    """Normaliza el resultado del Product Owner en el estado de flujo que sigue el loop."""
    output = output if output is not None else coordinator._product_owner_output(result)
    status = str(result.get("status") or "").strip().lower()
    output_status = str(output.get("status") or "").strip().lower()
    questions = output.get("questions") or result.get("questions") or []
    decisions = output.get("decisions") or result.get("blockingDecisions") or result.get("decisions") or []
    if status in {"runtime_unavailable", "failed_validation", "failed"}:
        return "blocked"
    if status in {"needs_input", "questions_required"} or output_status in {
        "needs_input",
        "questions_required",
    }:
        if not has_actionable_product_owner_input(questions=questions, decisions=decisions):
            raise ProductOwnerOutputValidationError(
                "ProductOwnerAgent needs_input output must include an actionable question or decision with options."
            )
        return "needs_input"
    if status == "blocked" and (questions or decisions):
        if not has_actionable_product_owner_input(questions=questions, decisions=decisions):
            raise ProductOwnerOutputValidationError(
                "ProductOwnerAgent blocked output must include an actionable question or decision with options."
            )
        return "needs_input"
    if status == "completed":
        return "backlog_ready"
    if status == "scope_is_clear" or output_status == "scope_is_clear":
        return "brief_ready"
    if status == "brief_ready" or output_status == "brief_ready":
        return "brief_ready"
    if status in {"backlog_ready", "completed"} or output_status in {"backlog_ready", "completed"}:
        return "backlog_ready"
    return "blocked"


def ensure_product_initiative(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    message: str,
    title: str,
    result: dict[str, Any],
    output: dict[str, Any],
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Resuelve la iniciativa del turno, reusando la que ya cubre el hilo antes de crear una nueva.

    Sin la reutilización por hilo cada turno arrancaría con una iniciativa vacía y el
    ProductOwner volvería a preguntar lo que ya se respondió.
    """
    initiative = result.get("initiative")
    if isinstance(initiative, dict) and initiative.get("id"):
        try:
            return stamp_initiative_thread(
                coordinator, coordinator.discovery.get_initiative(str(initiative["id"])), thread_id
            )
        except KeyError:
            pass
    initiative_id = result.get("initiativeId") or output.get("initiativeId")
    if initiative_id:
        try:
            return stamp_initiative_thread(
                coordinator, coordinator.discovery.get_initiative(str(initiative_id)), thread_id
            )
        except KeyError:
            pass
    if thread_id:
        existing = coordinator.discovery.find_initiative_by_thread(project_id, thread_id)
        if existing is not None:
            return existing
    metadata: dict[str, Any] = {"source": "product_loop_coordinator"}
    if thread_id:
        metadata["threadId"] = thread_id
    return coordinator.discovery.create_initiative(
        {
            "projectId": project_id,
            "title": title,
            "summary": output.get("summary") or message,
            "status": "discovery",
            "priority": "medium",
            "owner": PRODUCT_OWNER_AGENT_ID,
            "metadata": metadata,
        }
    )


def stamp_initiative_thread(
    coordinator: ProductLoopCoordinator, initiative: dict[str, Any], thread_id: str | None
) -> dict[str, Any]:
    """Estampa ``threadId`` en una iniciativa reusada que aún no lo tiene.

    El Runner real crea la iniciativa sin ``metadata.threadId``; sin este sello,
    ``find_initiative_by_thread`` falla el turno siguiente y el hilo arranca con una iniciativa
    nueva, perdiendo brief, preguntas y respuestas previas (el PO vuelve a preguntar todo).
    """
    if not thread_id:
        return initiative
    metadata = initiative.get("metadata") if isinstance(initiative.get("metadata"), dict) else {}
    if metadata.get("threadId") == thread_id:
        return initiative
    try:
        return coordinator.discovery.update_initiative(
            initiative["id"], {"metadata": {**metadata, "threadId": thread_id}}
        )
    except KeyError:
        return initiative


def brief_payload(*, title: str, result: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Arma el payload del product brief desde el brief del resultado o el parche de la salida."""
    brief = dict(result.get("brief") or output.get("productBriefPatch") or {})
    return {
        "title": str(brief.get("title") or title),
        "summary": str(brief.get("summary") or output.get("summary") or ""),
        "problemStatement": str(brief.get("problemStatement") or ""),
        "goals": list(brief.get("goals") or []),
        "targetUsers": list(brief.get("targetUsers") or []),
        "successMetrics": list(brief.get("successMetrics") or []),
        "scope": str(brief.get("scope") or ""),
        "outOfScope": str(brief.get("outOfScope") or ""),
    }


def persist_product_brief(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    initiative_id: str,
    title: str,
    result: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    """Devuelve el brief ya persistido por el runner o lo crea/actualiza en discovery."""
    brief = result.get("brief")
    if isinstance(brief, dict) and brief.get("id"):
        try:
            return coordinator.discovery.get_product_brief(str(brief["id"]))
        except KeyError:
            pass
    payload = brief_payload(title=title, result=result, output=output)
    return coordinator.discovery.upsert_product_brief(
        {
            "projectId": project_id,
            "initiativeId": initiative_id,
            "title": payload["title"],
            "status": "in_review",
            "summary": payload["summary"],
            "problemStatement": payload["problemStatement"],
            "goals": payload["goals"],
            "targetUsers": payload["targetUsers"],
            "successMetrics": payload["successMetrics"],
            "scope": payload["scope"],
            "outOfScope": payload["outOfScope"],
            "changeSummary": "ProductLoopCoordinator persisted ProductOwnerAgent brief.",
            "authoredBy": PRODUCT_OWNER_AGENT_ID,
        }
    )


def persist_product_owner_output_record(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    initiative_id: str,
    brief_id: str,
    output: dict[str, Any],
    result: dict[str, Any],
    artifact_id: str | None,
) -> dict[str, Any]:
    """Devuelve el registro de salida del PO ya persistido o crea uno nuevo en discovery."""
    existing = result.get("productOwnerOutput")
    if isinstance(existing, dict) and existing.get("id"):
        try:
            return coordinator.discovery.get_product_owner_output(str(existing["id"]))
        except KeyError:
            pass
    return coordinator.discovery.create_product_owner_output(
        {
            "projectId": project_id,
            "initiativeId": initiative_id,
            "briefId": brief_id,
            "status": str(output.get("status") or result.get("status") or "blocked"),
            "summary": str(output.get("summary") or ""),
            "confidence": str(output.get("confidence") or "low"),
            "questions": output.get("questions") or [],
            "assumptions": output.get("assumptions") or [],
            "decisions": output.get("decisions") or [],
            "productBriefPatch": output.get("productBriefPatch") or {},
            "epics": output.get("epics") or [],
            "userStories": output.get("userStories") or [],
            "risks": output.get("risks") or [],
            "recommendedNextAction": str(output.get("recommendedNextAction") or ""),
            "runtimeId": str((result.get("runtime") or {}).get("id") or ""),
            "outputArtifactId": artifact_id or str(result.get("outputArtifactId") or ""),
            "metadata": {"source": "product_loop_coordinator"},
        }
    )


def question_options(question: dict[str, Any]) -> list[str]:
    """Opciones no vacías de una pregunta o decisión (propias o en su metadata)."""
    metadata = question.get("metadata") if isinstance(question.get("metadata"), dict) else {}
    options = question.get("options") or metadata.get("options") or []
    return [str(item) for item in options if str(item).strip()]


def runner_persisted(result: dict[str, Any] | None, key: str, prefix: str) -> list[dict[str, Any]] | None:
    """Registros con id que el Runner real ya persistió, o ``None`` si no persistió (stub/tests).

    Cuando existen, son la fuente de verdad y se reusan; el ``output`` id-less del modelo se usa solo
    como camino de respaldo para el runner de prueba, que no toca la base.
    """
    if not isinstance(result, dict):
        return None
    records = [
        record
        for record in result.get(key) or []
        if isinstance(record, dict) and str(record.get("id") or "").startswith(prefix)
    ]
    return records or None


def reuse_persisted(item: dict[str, Any], prefix: str, getter: Any) -> dict[str, Any] | None:
    """Registro ya persistido cuyo id tiene ``prefix``; ``None`` si no aplica o no existe."""
    record_id = str(item.get("id") or "")
    if not record_id.startswith(prefix):
        return None
    try:
        return getter(record_id)
    except KeyError:
        return None


def decision_options_with_recommendation_fallback(record: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """Opciones para la decisión de hilo del PO: las ofrecidas, o la recomendación como única.

    Una decisión sin opciones nunca debe quedar sin nada preseleccionable: si el PO no ofreció
    opciones pero sí una recomendación (o un valor por defecto), esa recomendación se ofrece como
    la única opción; el texto libre de la barra de ejecución sigue disponible de todos modos.
    """
    options = question_options(record) or question_options(item)
    if options:
        return options
    record_metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    recommendation = str(
        record.get("decision")
        or record_metadata.get("recommendation")
        or item.get("recommendation")
        or item.get("defaultDecision")
        or ""
    ).strip()
    return [recommendation] if recommendation else []


def ensure_product_owner_thread_decision(
    threads: Any,
    thread_id: str,
    *,
    record_id: str,
    meta_key: str,
    title: str,
    prompt: str,
    options: list[str],
    source_message_id: str | None,
) -> None:
    """Crea la decisión de hilo para un registro del PO una sola vez (idempotente por ``record_id``)."""
    for decision in threads.list_decisions(thread_id):
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        if metadata.get(meta_key) == record_id:
            return
    decision_metadata = {"source": PRODUCT_OWNER_AGENT_ID, meta_key: record_id}
    if source_message_id:
        decision_metadata["sourceMessageId"] = source_message_id
    threads.create_decision(
        thread_id=thread_id, title=title, prompt=prompt, options=options, metadata=decision_metadata
    )


def persist_clarification_questions(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    initiative_id: str,
    output: dict[str, Any],
    thread_id: str | None,
    source_message_id: str | None = None,
    result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reusa las preguntas que el Runner real ya persistió; solo crea cuando no persistió (stub).

    El Runner ya inserta las preguntas de aclaración; iterar el ``output`` id-less del modelo volvía
    a insertarlas (filas duplicadas cada turno, y las del Runner quedaban ``open`` contaminando el
    prompt siguiente). Aquí se reusa el registro persistido por id y se surface como decisión de hilo
    una sola vez.
    """
    records: list[dict[str, Any]] = []
    threads = ThreadsRepository(coordinator.connection) if thread_id else None
    persisted = runner_persisted(result, "questions", "clarification-question-")
    for item in persisted if persisted is not None else (output.get("questions") or []):
        if not isinstance(item, dict):
            continue
        record = reuse_persisted(
            item, "clarification-question-", coordinator.discovery.get_clarification_question
        )
        if record is None:
            question_text = str(item.get("question") or item.get("prompt") or "").strip()
            if not question_text:
                continue
            record = coordinator.discovery.create_clarification_question(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "question": question_text,
                    "priority": item.get("priority") or "high" if item.get("blocking", True) else "medium",
                    "askedBy": PRODUCT_OWNER_AGENT_ID,
                    "metadata": {
                        "source": PRODUCT_OWNER_AGENT_ID,
                        "category": item.get("category") or "product",
                        "whyItMatters": item.get("whyItMatters") or "",
                        "blocking": bool(item.get("blocking", True)),
                        "options": question_options(item),
                        "recommendation": item.get("recommendation") or "",
                        "defaultDecision": item.get("defaultDecision") or "",
                        "confidence": item.get("confidence") or "low",
                    },
                }
            )
        records.append(record)
        if threads and thread_id:
            question_text = str(record.get("question") or item.get("question") or "")
            ensure_product_owner_thread_decision(
                threads,
                thread_id,
                record_id=record["id"],
                meta_key="clarificationQuestionId",
                title=question_text[:120],
                prompt=question_text,
                options=decision_options_with_recommendation_fallback(record, item),
                source_message_id=source_message_id,
            )
    return records


def persist_product_decisions(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    initiative_id: str,
    brief_id: str,
    output: dict[str, Any],
    thread_id: str | None,
    source_message_id: str | None = None,
    result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reusa las decisiones que el Runner real ya persistió; solo crea cuando no persistió (stub).

    Igual que las preguntas, iterar el ``output`` id-less duplicaba las filas que el Runner ya
    insertó y las decisiones escaladas aparecían dos veces en el hilo. Se reusa el registro por id;
    sus opciones vienen del ``metadata.options`` persistido (o del modelo en el camino de respaldo).
    """
    records: list[dict[str, Any]] = []
    threads = ThreadsRepository(coordinator.connection) if thread_id else None
    persisted = runner_persisted(result, "blockingDecisions", "product-decision-")
    for item in persisted if persisted is not None else (output.get("decisions") or []):
        if not isinstance(item, dict):
            continue
        record = reuse_persisted(item, "product-decision-", coordinator.discovery.get_product_decision)
        if record is None:
            title = str(item.get("title") or item.get("decision") or "").strip()
            if not title:
                continue
            blocking = bool(item.get("blocking", False))
            raw_status = str(item.get("status") or "").strip().lower()
            status = raw_status or ("proposed" if blocking else "accepted")
            record = coordinator.discovery.create_product_decision(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "briefId": brief_id,
                    "title": title,
                    "status": status,
                    "context": item.get("question") or title,
                    "decision": item.get("decision") or item.get("recommendation") or "",
                    "rationale": item.get("rationale") or "",
                    "consequences": item.get("consequences") or [],
                    "decidedBy": PRODUCT_OWNER_AGENT_ID if status in {"accepted", "resolved"} else "",
                    "decidedAt": utc_now() if status in {"accepted", "resolved"} else None,
                    "metadata": {
                        "source": PRODUCT_OWNER_AGENT_ID,
                        "blocking": blocking,
                        "confidence": item.get("confidence") or "low",
                        "category": item.get("category") or item.get("type") or "",
                        "impact": item.get("impact") or item.get("risk") or item.get("severity") or "",
                        "requiresResearch": bool(item.get("requiresResearch", False)),
                        "options": question_options(item),
                    },
                }
            )
        records.append(record)
        status = str(record.get("status") or "").strip().lower()
        if threads and thread_id and status not in {"accepted", "resolved"}:
            title = str(record.get("title") or item.get("title") or "")
            ensure_product_owner_thread_decision(
                threads,
                thread_id,
                record_id=record["id"],
                meta_key="productDecisionId",
                title=title[:120],
                prompt=str(record.get("context") or item.get("question") or title),
                options=decision_options_with_recommendation_fallback(record, item),
                source_message_id=source_message_id,
            )
    return records


def pending_product_owner_thread_decisions(
    coordinator: ProductLoopCoordinator, thread_id: str | None
) -> list[dict[str, Any]]:
    """Decisiones pendientes del hilo que levantó el ProductOwnerAgent."""
    if not thread_id:
        return []
    pending: list[dict[str, Any]] = []
    for decision in ThreadsRepository(coordinator.connection).list_decisions(thread_id):
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        if decision["status"] != "pending" or metadata.get("source") != PRODUCT_OWNER_AGENT_ID:
            continue
        pending.append(
            {
                "decisionId": decision["id"],
                "title": decision["title"],
                "prompt": decision["prompt"],
                "options": decision.get("options") or [],
                "clarificationQuestionId": metadata.get("clarificationQuestionId"),
                "productDecisionId": metadata.get("productDecisionId"),
                "initiativeId": metadata.get("initiativeId"),
            }
        )
    return pending


def persist_backlog_from_output(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    output: dict[str, Any],
    result: dict[str, Any],
    product_owner_output_id: str | None,
) -> list[dict[str, Any]]:
    """Devuelve el backlog ya persistido o crea épicas e historias desde la salida del PO."""
    persisted = result.get("epics")
    if (
        isinstance(persisted, list)
        and persisted
        and isinstance(persisted[0], dict)
        and "epic" in persisted[0]
    ):
        return persisted
    if not output.get("epics") or not output.get("userStories"):
        return []
    return persist_product_owner_backlog(
        coordinator.backlog,
        project_id=project_id,
        output=output,
        product_owner_output_id=product_owner_output_id,
    )
