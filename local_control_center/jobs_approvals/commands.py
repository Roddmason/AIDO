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
    try:
        return jobs.approve_action(job_id, action_id, reason=required_reason(body))
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
    try:
        return jobs.deny_action(job_id, action_id, reason=required_reason(body))
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
