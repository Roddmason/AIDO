"""Pure helpers shared by the Product Loop retry and plan-only remediation paths.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import SIMILARITY_ACTIONS


def backfill_details_from_durable(durable: dict[str, Any]) -> dict[str, Any]:
    """Rebuild similarity blocker details from a durable run."""
    functionality = (
        durable.get("existingFunctionality") if isinstance(durable.get("existingFunctionality"), dict) else {}
    )
    return {
        "decisionId": durable.get("decisionId"),
        "functionalityId": functionality.get("id"),
        "sourceThreadId": functionality.get("sourceThreadId"),
        "score": functionality.get("score"),
        "options": list(SIMILARITY_ACTIONS),
    }


def mark_thread_run_queued_best_effort(
    *,
    threads: ThreadsRepository,
    thread_id: str,
    event_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mark the thread queued and record the event, reporting failures instead of raising."""
    status_update: dict[str, Any] = {"status": "queued"}
    try:
        thread = threads.set_status(thread_id, "queued")
    except Exception as error:
        thread = threads.get_thread(thread_id)
        status_update = {
            "status": "failed",
            "reason": str(redact_secrets(str(error))),
            "threadId": thread_id,
        }
    event_update: dict[str, Any] = {"status": "recorded"}
    try:
        threads.record_event(
            thread_id=thread_id,
            type="run_queued",
            agent_role="aido_lead",
            payload=event_payload,
        )
    except Exception as error:
        event_update = {
            "status": "failed",
            "reason": str(redact_secrets(str(error))),
            "threadId": thread_id,
        }
        with suppress(Exception):
            threads.record_event(
                thread_id=thread_id,
                type="run_queue_event_failed",
                agent_role="aido_lead",
                payload={
                    **event_payload,
                    "reason": event_update["reason"],
                },
            )
    update_summary = {"status": status_update["status"], "thread": status_update, "event": event_update}
    if reason := status_update.get("reason") or event_update.get("reason"):
        update_summary["reason"] = reason
    return thread, update_summary


def resolved_similarity_decision_mode(*, threads: ThreadsRepository, decision_id: str) -> str | None:
    """Devuelve el modo elegido por el usuario si la decisión está resuelta y es válido."""
    if not decision_id:
        return None
    try:
        decision = threads.get_decision(decision_id)
    except KeyError:
        return None
    if str(decision.get("status") or "") != "resolved":
        return None
    mode = str(decision.get("resolution") or "").strip().lower().replace(" ", "_").replace("-", "_")
    return mode if mode in SIMILARITY_ACTIONS else None


def cooldown_rejected_provider_ids(details: dict[str, Any]) -> set[str]:
    """ProviderIds rechazados en un bloqueo `resource_manager`, según sus `resourceBlockers`."""
    resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
    return {
        str(rejected["providerId"])
        for blocker in resource_blockers or []
        if isinstance(blocker, dict) and isinstance(blocker.get("decision"), dict)
        for rejected in blocker["decision"].get("rejected") or []
        if isinstance(rejected, dict) and str(rejected.get("providerId") or "").strip()
    }


def has_delivery_gitleaks_evidence(durable: dict[str, Any]) -> bool:
    """Return true when a durable run holds delivery gitleaks evidence."""
    review = durable.get("review") if isinstance(durable.get("review"), dict) else {}
    return (
        isinstance(durable.get("runtimeResult"), dict)
        and isinstance(durable.get("teamSchedule"), dict)
        and isinstance(review, dict)
        and bool(review.get("changedFiles"))
        and bool(str(durable.get("workspaceId") or "").strip())
        and bool(str(durable.get("workspacePath") or "").strip())
    )


def has_delivery_resource_learning_evidence(durable: dict[str, Any]) -> bool:
    """Return true when a durable run holds delivery resource-learning evidence."""
    resource_details = (
        durable.get("resource_learning") if isinstance(durable.get("resource_learning"), dict) else {}
    )
    review = (
        durable.get("review") if isinstance(durable.get("review"), dict) else resource_details.get("review")
    )
    gitleaks = (
        durable.get("gitleaks")
        if isinstance(durable.get("gitleaks"), dict)
        else resource_details.get("gitleaks")
    )
    return (
        isinstance(durable.get("runtimeResult"), dict)
        and (
            isinstance(durable.get("teamSchedule"), dict)
            or isinstance(resource_details.get("teamSchedule"), dict)
        )
        and isinstance(review, dict)
        and bool(review.get("changedFiles"))
        and isinstance(gitleaks, dict)
        and gitleaks.get("status") == "completed"
    )


def source_retry_message(
    *,
    threads: ThreadsRepository,
    thread_id: str,
    message_id: str,
) -> dict[str, Any] | None:
    """Return the original user message a retry should replay."""
    if message_id:
        return threads.get_message(message_id)
    user_messages = [message for message in threads.list_messages(thread_id) if message["kind"] == "user"]
    return user_messages[-1] if user_messages else None


def existing_retry_job(
    *,
    jobs: JobsRepository,
    project_id: str,
    loop_id: str,
    action_id: str,
) -> dict[str, Any] | None:
    """Return the active retry job for a thread, if any."""
    active_statuses = {"approval_required", "queued", "running"}
    for job in jobs.list_jobs(project_id=project_id):
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if (
            job["kind"] == "thread.product_loop.run"
            and payload.get("retryOfLoopId") == loop_id
            and payload.get("remediationActionId") == action_id
            and job["status"] in active_statuses
        ):
            return job
    return None


def existing_plan_only_job(
    *,
    jobs: JobsRepository,
    project_id: str,
    loop_id: str,
    action_id: str,
) -> dict[str, Any] | None:
    """Return the active plan-only job for a thread, if any."""
    active_statuses = {"approval_required", "queued", "running"}
    for job in jobs.list_jobs(project_id=project_id):
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if (
            job["kind"] == "thread.product_loop.run"
            and payload.get("planOnlyOfLoopId") == loop_id
            and payload.get("remediationActionId") == action_id
            and job["status"] in active_statuses
        ):
            return job
    return None
