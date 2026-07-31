"""Fase de intake: normaliza el mensaje, persiste thread+loop y aplica la puerta de similitud.

Fase del pipeline spec-driven extraída de ``coordinator.py``: cada función recibe el coordinator
(driver del FSM y dueño de la transacción) y el ``run`` mutable, y devuelve un dict terminal o
``None`` para continuar. Los helpers y constantes compartidos se importan de forma diferida desde
el coordinator para evitar el ciclo de imports (el coordinator importa este módulo al cargar).

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun

__all__ = ["ensure_thread_and_similarity"]


def ensure_thread_and_similarity(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Normaliza el mensaje, persiste thread+loop y aplica la puerta de funcionalidad existente.

    Devuelve el resultado terminal del bloqueo por similitud o ``None`` para continuar.
    """
    from local_control_center.product_loop.coordinator import INITIAL_STATE, ProductLoopTransitionError
    from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
    from local_control_center.shared.redaction import redact_secrets

    message = run.message
    root = run.root
    title = run.title
    run_metadata = run.run_metadata
    project_id = run.project_id
    session_id = run.session_id
    thread_id = run.thread_id
    actor = run.actor
    message_text = str(message or "").strip()
    if not message_text:
        raise ProductLoopTransitionError("Product Loop user message is required.")
    effective_root = Path(root).resolve(strict=False) if root is not None else coordinator.root
    resolved_title = title or message_text.splitlines()[0][:80] or "Product Loop"
    request_meta = coordinator._sanitize_untrusted_resource_approval_metadata(
        strip_untrusted_resource_cost_policy_metadata(redact_secrets(run_metadata or {}))
    )
    plan_only = bool(request_meta.get("planOnly") or request_meta.get("plan_only"))
    thread = coordinator._create_thread(
        project_id=project_id,
        message=message_text,
        title=resolved_title,
        session_id=session_id,
        thread_id=thread_id,
        message_id=request_meta.get("messageId") if isinstance(request_meta.get("messageId"), str) else None,
        message_metadata=request_meta,
        actor=actor,
    )
    thread_id = thread["projectThreadId"]
    coordinator._supersede_interrupted_loops(project_id=project_id, thread_id=thread_id, actor=actor)
    loop = coordinator.start(
        project_id=project_id,
        title=resolved_title,
        context={
            "durableRun": {
                "status": INITIAL_STATE,
                "thread": thread,
                "message": message_text,
                "requestMeta": request_meta,
                "planOnly": plan_only,
                "evidencePackageIds": [],
                # Re-entry durable: True mientras el run ejecuta; _run_result lo apaga en todo
                # cierre controlado. Un True huerfano tras un crash marca el loop como
                # interrumpido y el proximo run del hilo lo supersede.
                "runActive": True,
            }
        },
        correlation_id=thread_id,
        actor=actor,
        reason="Product Loop started from a user message.",
    )
    coordinator._record_loop_event(
        project_id=project_id,
        event_type="product_loop.message_received",
        loop_id=loop["id"],
        payload={"thread": thread},
        thread_id=thread_id,
    )
    existing_functionality = (
        None
        if coordinator._memory_decision_resolved(request_meta)
        else coordinator._existing_functionality_match(project_id=project_id, message=message_text)
    )
    if existing_functionality is not None:
        return coordinator._block_existing_functionality(
            loop=loop,
            thread_id=thread_id,
            functionality=existing_functionality,
            actor=actor,
        )
    run.message_text = message_text
    run.effective_root = effective_root
    run.resolved_title = resolved_title
    run.request_meta = request_meta
    run.plan_only = plan_only
    run.thread = thread
    run.thread_id = thread_id
    run.loop = loop
    return None
