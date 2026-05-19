from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from local_control_center.store import PlatformStore


def list_jobs(store: "PlatformStore") -> dict[str, Any]:
    return {"jobs": store.list_jobs(), "events": store.list_events()}


def create_job(store: "PlatformStore", body: dict[str, Any]) -> dict[str, Any]:
    return store.create_job(
        project_id=body["projectId"],
        kind=body["kind"],
        payload=body.get("payload") or {},
        idempotency_key=body.get("idempotencyKey"),
    )


def approve_job(store: "PlatformStore", job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return store.approve_job(job_id, reason=body.get("reason", ""))


def cancel_job(store: "PlatformStore", job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return store.cancel_job(job_id, reason=body.get("reason", ""))


def retry_job(store: "PlatformStore", job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return store.retry_job(job_id, reason=body.get("reason", ""))


def list_approvals(store: "PlatformStore") -> dict[str, Any]:
    return {"actionRequests": store.list_action_requests()}


def approve_action(
    store: "PlatformStore",
    job_id: str,
    action_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    return store.approve_action(job_id, action_id, reason=body.get("reason", ""))


def deny_action(
    store: "PlatformStore",
    job_id: str,
    action_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    return store.deny_action(job_id, action_id, reason=body.get("reason", ""))
