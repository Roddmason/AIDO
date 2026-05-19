from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from local_control_center.shared.event_bus import EventBus

from .repository import JobsRepository


def required_reason(body: dict[str, Any]) -> str:
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Approval reason is required.")
    return reason


def list_jobs(jobs: JobsRepository, events: EventBus) -> dict[str, Any]:
    return {"jobs": jobs.list_jobs(), "events": events.list_events()}


def create_job(jobs: JobsRepository, body: dict[str, Any]) -> dict[str, Any]:
    return jobs.create_job(
        project_id=body["projectId"],
        kind=body["kind"],
        payload=body.get("payload") or {},
        idempotency_key=body.get("idempotencyKey"),
        workflow_run_id=body.get("workflowRunId"),
        workflow_step_id=body.get("workflowStepId"),
    )


def approve_job(jobs: JobsRepository, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return jobs.approve_job(job_id, reason=required_reason(body))


def cancel_job(jobs: JobsRepository, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return jobs.cancel_job(job_id, reason=body.get("reason", ""))


def retry_job(jobs: JobsRepository, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return jobs.retry_job(job_id, reason=body.get("reason", ""))


def list_approvals(jobs: JobsRepository) -> dict[str, Any]:
    return {"actionRequests": jobs.list_action_requests()}


def approve_action(
    jobs: JobsRepository,
    job_id: str,
    action_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    return jobs.approve_action(job_id, action_id, reason=required_reason(body))


def deny_action(
    jobs: JobsRepository,
    job_id: str,
    action_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    return jobs.deny_action(job_id, action_id, reason=body.get("reason", ""))
