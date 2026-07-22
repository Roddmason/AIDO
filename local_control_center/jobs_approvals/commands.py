"""Capa de comandos: traduce el cuerpo HTTP a llamadas del repositorio y mapea sus errores.

Cada comando valida lo mínimo (p. ej. razón obligatoria en aprobaciones), delega la
transacción en `JobsRepository` y convierte los `ValueError` de conflicto de estado en
`HTTPException` 409/422. No conoce SQL ni el connection: ese contrato vive en el repositorio.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from local_control_center.shared.event_bus import EventBus

from .repository import JobsRepository


def required_reason(body: dict[str, Any]) -> str:
    """Extrae y normaliza la razón del cuerpo.

    Raises:
        HTTPException: 422 si la razón está vacía o solo contiene espacios.
    """
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Approval reason is required.")
    return reason


def list_jobs(jobs: JobsRepository, events: EventBus) -> dict[str, Any]:
    """Devuelve todos los jobs junto con el stream de eventos actual."""
    return {"jobs": jobs.list_jobs(), "events": events.list_events()}


def create_job(jobs: JobsRepository, body: dict[str, Any]) -> dict[str, Any]:
    """Encola un job a partir del request, mapeando los alias camelCase a los argumentos del repositorio."""
    return jobs.create_job(
        project_id=body["projectId"],
        kind=body["kind"],
        payload=body.get("payload") or {},
        idempotency_key=body.get("idempotencyKey"),
        workflow_run_id=body.get("workflowRunId"),
        workflow_step_id=body.get("workflowStepId"),
    )


def approve_job(jobs: JobsRepository, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Aprueba el job a nivel general; exige razón."""
    return jobs.approve_job(job_id, reason=required_reason(body))


def cancel_job(jobs: JobsRepository, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Cancela el job y libera su lease; la razón es opcional."""
    return jobs.cancel_job(job_id, reason=body.get("reason", ""))


def retry_job(jobs: JobsRepository, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Reencola el job (o vuelve a approval_required si quedan acciones pendientes)."""
    return jobs.retry_job(job_id, reason=body.get("reason", ""))


def list_approvals(jobs: JobsRepository) -> dict[str, Any]:
    """Devuelve las action requests que componen la cola de aprobaciones."""
    return {"actionRequests": jobs.list_action_requests()}


def _product_loop_delivery_loop_id(action: dict[str, Any]) -> str:
    if action.get("actionType") != "product_loop.approve_delivery":
        return ""
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    return str(payload.get("loopId") or "").strip()


def _apply_product_loop_delivery_feedback(
    jobs: JobsRepository,
    *,
    job_id: str,
    action_id: str,
    action_name: str,
    reason: str,
) -> dict[str, Any] | None:
    action = jobs.get_action_request(action_id)
    loop_id = _product_loop_delivery_loop_id(action)
    if not loop_id:
        return None
    if action["jobId"] != job_id:
        raise HTTPException(status_code=404, detail=f"Action {action_id} does not belong to job {job_id}")
    from local_control_center.product_loop.coordinator import (
        ProductLoopCoordinator,
        ProductLoopStopConditionError,
        ProductLoopTransitionError,
    )

    try:
        ProductLoopCoordinator(jobs.connection).apply_feedback(
            loop_id,
            action=action_name,
            feedback=reason,
            actor="operator",
            target_type="loop",
            target_id=loop_id,
        )
    except (KeyError, ProductLoopStopConditionError, ProductLoopTransitionError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "job": jobs.get_job(job_id),
        "actionRequest": jobs.get_action_request(action_id),
    }


def _apply_product_loop_resource_approval(
    jobs: JobsRepository,
    *,
    job_id: str,
    action_id: str,
    reason: str,
) -> dict[str, Any] | None:
    """Aprueba una selección de recursos bloqueada ejecutando sus remediaciones persistidas.

    Reutiliza las remediaciones del thread en vez de duplicar sus reglas: `approve_resource_decision`
    estampa la aprobación durable con la selección auditada y `retry_loop` reencola el run con la
    provenance que el coordinator exige para confiar en `approvedResourceSelections`.

    Returns:
        None cuando la acción no es una aprobación de recursos del Product Loop.

    Raises:
        HTTPException: 404 si la acción no pertenece al job; 409 si la acción ya fue decidida,
            el loop ya no está bloqueado en resource_manager, o una remediación no puede ejecutarse.
    """
    action = jobs.get_action_request(action_id)
    if action.get("actionType") != "product_loop.approve_resource_decision":
        return None
    if action["jobId"] != job_id:
        raise HTTPException(status_code=404, detail=f"Action {action_id} does not belong to job {job_id}")
    if action["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Action request is already {action['status']}.")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    loop_id = str(payload.get("loopId") or "").strip()
    thread_id = str(payload.get("threadId") or "").strip()
    if not loop_id or not thread_id:
        raise HTTPException(
            status_code=409, detail="Resource approval action is missing its loopId/threadId payload."
        )
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator
    from local_control_center.remediations.service import BlockerRemediationService

    coordinator = ProductLoopCoordinator(jobs.connection)
    try:
        loop = coordinator.get(loop_id)
    except KeyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    durable = (loop.get("context") or {}).get("durableRun") or {}
    if loop["state"] != "blocked" or durable.get("blockedStage") != "resource_manager":
        raise HTTPException(
            status_code=409,
            detail="Product Loop is no longer blocked at resource_manager; deny this approval instead.",
        )
    service = BlockerRemediationService(jobs.connection)
    pending = [
        item
        for item in service.repository.list_for_thread(thread_id)
        if item.get("status") == "pending" and item.get("loopId") == loop_id
    ]
    approve_card = next(
        (item for item in pending if item.get("actionType") == "approve_resource_decision"), None
    )
    resource_approval = (
        durable.get("resourceApproval") if isinstance(durable.get("resourceApproval"), dict) else {}
    )
    if approve_card is not None:
        # object(): approve_resource_decision y retry_loop no consultan el runtime de la plataforma.
        execution = (service.execute(approve_card["id"], platform=object()) or {}).get("execution") or {}
        if execution.get("status") != "completed":
            raise HTTPException(
                status_code=409,
                detail=str(execution.get("reason") or "Resource approval remediation failed."),
            )
    elif resource_approval.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="No pending approve_resource_decision remediation exists for this loop.",
        )
    retry_card = next((item for item in pending if item.get("actionType") == "retry_loop"), None)
    if retry_card is not None:
        execution = (service.execute(retry_card["id"], platform=object()) or {}).get("execution") or {}
        if execution.get("status") not in {"queued", "completed"}:
            raise HTTPException(
                status_code=409,
                detail=str(execution.get("reason") or "Product Loop retry could not be queued."),
            )
    try:
        return jobs.approve_action(job_id, action_id, reason=reason)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def approve_action(
    jobs: JobsRepository,
    job_id: str,
    action_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Aprueba una action request y emite su permission grant.

    Raises:
        HTTPException: 422 si falta la razón; 409 si la acción ya fue decidida o expiró.
    """
    reason = required_reason(body)
    product_loop_result = _apply_product_loop_delivery_feedback(
        jobs,
        job_id=job_id,
        action_id=action_id,
        action_name="accept",
        reason=reason,
    )
    if product_loop_result is not None:
        return product_loop_result
    resource_approval_result = _apply_product_loop_resource_approval(
        jobs,
        job_id=job_id,
        action_id=action_id,
        reason=reason,
    )
    if resource_approval_result is not None:
        return resource_approval_result
    try:
        return jobs.approve_action(job_id, action_id, reason=reason)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def deny_action(
    jobs: JobsRepository,
    job_id: str,
    action_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Deniega una action request y cancela el job asociado.

    Raises:
        HTTPException: 422 si falta la razón; 409 si la acción ya fue decidida.
    """
    reason = required_reason(body)
    product_loop_result = _apply_product_loop_delivery_feedback(
        jobs,
        job_id=job_id,
        action_id=action_id,
        action_name="request_changes",
        reason=reason,
    )
    if product_loop_result is not None:
        return product_loop_result
    try:
        return jobs.deny_action(job_id, action_id, reason=reason)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
