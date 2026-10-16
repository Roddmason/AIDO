"""Reintentos de un Product Loop bloqueado: retry, gates de entrega y continuación plan-only.

Familia extraída de ``remediations/service.py``: cada función recibe el ``BlockerRemediationService``
(dueño de la conexión, del repositorio de acciones y de la fábrica de coordinators) y re-valida que
la acción siga vigente y el loop siga bloqueado en la misma etapa antes de reanudarlo:
retry genérico o de research verificada, re-aprobación de la entrega tras gitleaks o aprendizaje de
recursos, y continuación en modo plan-only. Lo que el servicio expone o los tests parchean sigue
disponible como delegador en la clase.

@author Rodrigo Mason
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.payloads import runtime_risk_review_failure
from local_control_center.remediations.retry_support import (
    existing_plan_only_job,
    existing_retry_job,
    has_delivery_gitleaks_evidence,
    has_delivery_resource_learning_evidence,
    mark_thread_run_queued_best_effort,
    resolved_similarity_decision_mode,
    source_retry_message,
)
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

if TYPE_CHECKING:
    from local_control_center.remediations.service import BlockerRemediationService

__all__ = [
    "blocked_gitleaks_context",
    "continue_plan_only",
    "retry_delivery_approval",
    "retry_gitleaks_approval",
    "retry_loop",
    "retry_research_loop",
    "retry_resource_learning_approval",
    "retry_root",
]


def retry_research_loop(
    service: BlockerRemediationService, *, action: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Reanuda un loop bloqueado en research con el reporte verificado que eligió el operador."""
    from local_control_center.product_loop.coordinator import _UserMessageRun
    from local_control_center.product_loop.phases.discovery import finish_brief_ready
    from local_control_center.product_loop.research_resolution import (
        ResearchResolutionError,
        hydrate_research_run,
        resolution_snapshot,
        validate_research,
    )

    blocked = {"status": "blocked", "action": "retry_loop"}
    research_id = payload.get("researchRunId")
    if not isinstance(research_id, str) or not research_id.strip():
        return {
            **blocked,
            "reason": "Choose a verified research report explicitly before continuing this loop.",
        }
    if service.root is None:
        return {**blocked, "reason": "The project root is required to verify research artifacts."}
    coordinator = service._coordinator()
    threads = ThreadsRepository(service.connection)
    try:
        with immediate_transaction(service.connection):
            current = service.repository.get(action["id"])
            loop = coordinator.get(current["loopId"])
            durable = dict(loop["context"].get("durableRun") or {})
            if (
                current["status"] != "pending"
                or loop["state"] != "blocked"
                or durable.get("blockedStage") != "research"
                or durable.get("runActive")
                or current["projectId"] != loop["projectId"]
            ):
                raise ResearchResolutionError("This research gate is no longer blocked and pending.")
            snapshot = resolution_snapshot(service.connection, loop)
            if (
                snapshot != current["payload"].get("researchSnapshot")
                or loop["version"] != current["payload"].get("researchLoopVersion")
                or snapshot["threadId"] != current["threadId"]
            ):
                raise ResearchResolutionError(
                    "The loop, policy, or decision snapshot changed; review research again."
                )
            thread = threads.get_thread(snapshot["threadId"])
            if thread["status"] not in {"blocked", "open", "resolved"}:
                raise ResearchResolutionError("The thread is not available for research continuation.")
            newer = service.connection.execute(
                "SELECT 1 FROM product_loops WHERE project_id=? AND rowid>(SELECT rowid FROM product_loops WHERE id=?) "
                "AND json_extract(context,'$.durableRun.thread.projectThreadId')=? LIMIT 1",
                (loop["projectId"], loop["id"], snapshot["threadId"]),
            ).fetchone()
            active_job = service.connection.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND kind='thread.product_loop.run' "
                "AND status IN ('queued','running','resource_wait') "
                "AND json_extract(payload,'$.threadId')=? LIMIT 1",
                (loop["projectId"], snapshot["threadId"]),
            ).fetchone()
            if newer or active_job:
                raise ResearchResolutionError("A newer loop or active continuation already owns this thread.")
            validated = validate_research(
                service.connection, root=service.root, snapshot=snapshot, research_run_id=research_id.strip()
            )
            receipt = {
                **validated,
                "id": f"research-resolution-{uuid.uuid4()}",
                "loopId": loop["id"],
                "productOwnerOutputId": snapshot["productOwnerOutputId"],
                "snapshot": snapshot,
                "loopVersion": loop["version"],
                "remediationActionId": current["id"],
                "adoptedAt": utc_now(),
                "status": "consumed",
            }
            if durable["productOwner"]["status"] == "backlog_ready":
                marker = {"loopId": loop["id"], "receiptId": receipt["id"]}
                metadata = {"researchContinuation": marker, "messageId": snapshot["messageId"]}
                job = JobsRepository(service.connection).create_job(
                    project_id=loop["projectId"],
                    kind="thread.product_loop.run",
                    payload={
                        "projectId": loop["projectId"],
                        "threadId": snapshot["threadId"],
                        "messageId": snapshot["messageId"],
                        "message": durable["message"],
                        "root": str(service.root),
                        "title": loop["title"],
                        "runMetadata": metadata,
                        "remediationActionId": current["id"],
                    },
                    idempotency_key=f"research-resolution:{loop['id']}:{current['id']}",
                )["job"]
                receipt.update({"status": "queued", "jobId": job["id"]})
            durable["researchResolution"] = receipt
            loop = coordinator.repository.update_loop_context(
                loop["id"],
                context={**loop["context"], "durableRun": durable},
            )
            if receipt["status"] == "queued":
                thread = threads.set_status(snapshot["threadId"], "queued")
                execution = {
                    "status": "queued",
                    "action": "retry_loop",
                    "job": job,
                    "loop": loop,
                    "thread": thread,
                    "reason": "Queued continuation from the persisted ProductOwner output.",
                }
            else:
                run = _UserMessageRun(
                    project_id=loop["projectId"], message=durable["message"], actor="operator"
                )
                hydrate_research_run(coordinator, run, loop=loop, receipt=receipt)
                result = finish_brief_ready(coordinator, run, in_transaction=True)
                thread = threads.set_status(snapshot["threadId"], "open")
                execution = {
                    "status": "completed",
                    "action": "retry_loop",
                    "loop": result["loop"],
                    "thread": thread,
                    "reason": "Research verified; the persisted brief is ready.",
                }
            threads.record_event(
                thread_id=snapshot["threadId"],
                type="research_adopted",
                agent_role="aido_lead",
                payload={
                    "loopId": loop["id"],
                    "researchRunId": research_id,
                    "receiptId": receipt["id"],
                    "status": execution["status"],
                },
            )
            service.connection.execute(
                "UPDATE remediation_actions SET status='resolved', resolved_at=? "
                "WHERE loop_id=? AND stage='research' AND status='pending'",
                (utc_now(), loop["id"]),
            )
            return execution
    except (ResearchResolutionError, KeyError, OSError, ValueError, TypeError) as error:
        return {**blocked, "reason": str(error)}


def retry_loop(
    service: BlockerRemediationService, *, action: dict[str, Any], payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Reintenta el loop bloqueado de la acción, ruteando research y los gates de entrega a su variante."""
    if action.get("stage") == "research":
        return retry_research_loop(service, action=action, payload=payload or {})
    loop_id = str(action.get("loopId") or "").strip()
    if not loop_id:
        return {"status": "blocked", "action": "retry_loop", "reason": "loopId is required."}
    coordinator = service._coordinator()
    threads = ThreadsRepository(service.connection)
    jobs = JobsRepository(service.connection)
    with immediate_transaction(service.connection):
        current_action = service.repository.get(action["id"])
        if current_action["status"] != "pending":
            return {
                "status": "blocked",
                "action": "retry_loop",
                "remediationStatus": current_action["status"],
                "reason": "Only pending remediation actions can be executed.",
            }
        loop = coordinator.get(loop_id)
        if loop["state"] != "blocked":
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Product Loop is not blocked.",
            }
        if (
            current_action.get("loopId") != loop_id
            or current_action.get("projectId") != loop["projectId"]
            or current_action.get("actionType") != "retry_loop"
        ):
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Remediation action no longer matches the blocked Product Loop.",
            }

        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        remediation_stage = str(current_action.get("stage") or "").strip()
        blocked_stage = str(durable.get("blockedStage") or "").strip()
        if blocked_stage == "resource_manager" and runtime_risk_review_failure(
            durable.get("resource_manager") or {}
        ):
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "The exact runtime risk proposals require explicit review; generic retry cannot replace this consent.",
            }
        if remediation_stage and blocked_stage and remediation_stage != blocked_stage:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Remediation stage no longer matches the current Product Loop blocker.",
                "remediationStage": remediation_stage,
                "blockedStage": blocked_stage,
            }
        thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
        request_meta = (
            strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta"))
            if isinstance(durable.get("requestMeta"), dict)
            else {}
        )
        action_thread_id = str(current_action.get("threadId") or "").strip()
        durable_thread_id = str(thread_ref.get("projectThreadId") or "").strip()
        if action_thread_id and durable_thread_id and action_thread_id != durable_thread_id:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Remediation thread no longer matches the blocked Product Loop thread.",
            }
        thread_id = action_thread_id or durable_thread_id
        if not thread_id:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Blocked Product Loop does not reference a project thread.",
            }

        thread = threads.get_thread(thread_id)
        if thread["projectId"] != loop["projectId"]:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Thread project does not match the blocked Product Loop project.",
            }
        retryable_thread_statuses = {"open", "blocked"}
        if blocked_stage == "functionality_memory":
            retryable_thread_statuses.add("waiting_decision")
        if thread["status"] not in retryable_thread_statuses:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": (f"Thread status {thread['status']} cannot be superseded by a retry."),
            }

        if blocked_stage == "approval":
            return retry_delivery_approval(
                service,
                action=current_action,
                coordinator=coordinator,
                loop=loop,
                durable=durable,
                thread_id=thread_id,
                threads=threads,
            )
        if blocked_stage == "resource_learning" and has_delivery_resource_learning_evidence(durable):
            return retry_resource_learning_approval(
                service,
                action=current_action,
                coordinator=coordinator,
                loop=loop,
                durable=durable,
                thread_id=thread_id,
                threads=threads,
            )

        functionality_decision_mode: str | None = None
        if blocked_stage == "functionality_memory":
            functionality_decision_mode = resolved_similarity_decision_mode(
                threads=threads,
                decision_id=str(durable.get("decisionId") or ""),
            )
            if functionality_decision_mode is None:
                return {
                    "status": "blocked",
                    "action": "retry_loop",
                    "reason": (
                        "Resolve the existing-functionality decision on this thread before retrying the loop."
                    ),
                    "decisionId": durable.get("decisionId"),
                }

        message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
        message = str(durable.get("message") or "").strip()
        source_message = source_retry_message(
            threads=threads,
            thread_id=thread_id,
            message_id=message_id,
        )
        if source_message is not None:
            message_id = source_message["id"]
            message = message or str(source_message["content"] or "").strip()
        if not message_id or not message:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Blocked Product Loop is missing the original thread message.",
            }

        root = retry_root(service, loop["projectId"])
        if not root:
            return {
                "status": "blocked",
                "action": "retry_loop",
                "reason": "Project root is required to queue a real Product Loop retry.",
            }

        plan_only = bool(
            durable.get("planOnly") or request_meta.get("planOnly") or request_meta.get("plan_only")
        )
        approved_resource_selections = service._approved_resource_selections_from_durable(durable)
        retry_metadata = {
            **request_meta,
            "messageId": message_id,
            "planOnly": plan_only,
            "approvedResourceSelections": approved_resource_selections,
            "retryOfLoopId": loop_id,
            "retryStage": blocked_stage,
            "retryReason": durable.get("blockedReason"),
            "remediationActionId": current_action["id"],
        }
        if functionality_decision_mode:
            retry_metadata["functionalityDecision"] = functionality_decision_mode
        retry_metadata = service._reseal_operator_cost_decision(
            retry_metadata,
            project_id=loop["projectId"],
            source_message=source_message,
            refresh_global_team=True,
        )

        existing_job = existing_retry_job(
            jobs=jobs,
            project_id=loop["projectId"],
            loop_id=loop_id,
            action_id=current_action["id"],
        )
        if existing_job is None:
            queued_at = utc_now()
            retry_metadata["retryQueuedAt"] = queued_at
            created = jobs.create_job(
                project_id=loop["projectId"],
                kind="thread.product_loop.run",
                payload={
                    "threadId": thread_id,
                    "messageId": message_id,
                    "projectId": loop["projectId"],
                    "message": message,
                    "title": thread["title"] or loop["title"],
                    "root": root,
                    "decision": request_meta.get("decision"),
                    "teamPlan": request_meta.get("teamPlan"),
                    "planOnly": plan_only,
                    "approvedResourceSelections": approved_resource_selections,
                    "runMetadata": retry_metadata,
                    "retryOfLoopId": loop_id,
                    "retryStage": durable.get("blockedStage"),
                    "retryReason": durable.get("blockedReason"),
                    "remediationActionId": current_action["id"],
                    "retryQueuedAt": queued_at,
                },
                idempotency_key=f"product-loop-retry:{loop_id}:{current_action['id']}",
            )
            retry_job = created["job"]
        else:
            queued_at = utc_now()
            retry_job = existing_job

        retry_ref = {
            "status": "queued",
            "jobId": retry_job["id"],
            "threadId": thread_id,
            "messageId": message_id,
            "remediationActionId": current_action["id"],
            "queuedAt": queued_at,
        }
        durable = {
            **durable,
            "status": "retry_queued",
            "retry": retry_ref,
            "updatedAt": queued_at,
        }
        thread = threads.set_status(thread_id, "queued")
        threads.record_event(
            thread_id=thread_id,
            type="run_queued",
            agent_role="aido_lead",
            payload={
                "jobId": retry_job["id"],
                "messageId": message_id,
                "status": retry_job["status"],
                "retryOfLoopId": loop_id,
                "remediationActionId": current_action["id"],
            },
        )
        superseded = coordinator.transition_in_transaction(
            loop_id,
            to_state="cancelled",
            reason="Blocked Product Loop superseded by a queued retry job.",
            actor="remediation",
            trigger="retry_loop",
            context_patch={"durableRun": durable},
            metadata={
                "remediationActionId": current_action["id"],
                "retryJobId": retry_job["id"],
            },
            expected_version=loop["version"],
        )
    return {
        "status": "queued",
        "action": "retry_loop",
        "reason": "Queued a real Product Loop retry job.",
        "job": retry_job,
        "loop": superseded,
        "thread": thread,
        "threadStatusUpdate": {
            "status": "queued",
            "thread": {"status": "queued"},
            "event": {"status": "recorded"},
        },
        "retryOfLoopId": loop_id,
        "threadId": thread_id,
        "messageId": message_id,
    }


def retry_delivery_approval(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    coordinator: Any,
    loop: dict[str, Any],
    durable: dict[str, Any],
    thread_id: str,
    threads: ThreadsRepository,
) -> dict[str, Any]:
    """Recrea la aprobación de entrega de un loop bloqueado por ``approval_unavailable`` desde su evidencia."""
    if not service.connection.in_transaction:
        raise RuntimeError("_retry_delivery_approval requires an active transaction.")
    if action.get("stage") != "approval" or action.get("blockerType") != "approval_unavailable":
        return {
            "status": "blocked",
            "action": "retry_loop",
            "reason": "Remediation action does not match the delivery approval blocker.",
        }
    approval = durable.get("approval") if isinstance(durable.get("approval"), dict) else {}
    evidence_refs = [str(item) for item in approval.get("evidenceRefs") or [] if str(item).strip()]
    diff_refs = [item for item in approval.get("diffRefs") or [] if item]
    changed_files = [str(item) for item in approval.get("changedFiles") or [] if str(item).strip()]
    workspace_id = str(approval.get("workspaceId") or durable.get("workspaceId") or "").strip()
    if not evidence_refs or not diff_refs or not changed_files:
        return {
            "status": "blocked",
            "action": "retry_loop",
            "reason": "Blocked approval retry is missing evidenceRefs, diffRefs, or changedFiles.",
            "loopId": loop["id"],
        }

    approval_payload = {
        "loopId": loop["id"],
        "workspaceId": workspace_id,
        "evidenceRefs": evidence_refs,
        "diffRefs": diff_refs,
        "changedFiles": changed_files,
        "retryOfApprovalUnavailableLoopId": loop["id"],
        "remediationActionId": action["id"],
    }
    jobs = JobsRepository(service.connection)
    approval_job = jobs.create_job(
        project_id=loop["projectId"],
        kind="product_loop_delivery_approval",
        status="approval_required",
        payload=approval_payload,
    )["job"]
    approval_action = jobs.create_action_request(
        job_id=approval_job["id"],
        project_id=loop["projectId"],
        action_type="product_loop.approve_delivery",
        risk_level="medium",
        command="approve product loop delivery",
        payload=approval_payload,
        reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
    )
    next_approval = {
        "status": "available",
        "jobId": approval_job["id"],
        "actionRequestId": approval_action["id"],
        "evidenceRefs": evidence_refs,
        "diffRefs": diff_refs,
    }
    now = utc_now()
    review_durable = {
        **durable,
        "status": "review_ready",
        "approval": next_approval,
        "updatedAt": now,
    }
    review_ready = coordinator.transition_in_transaction(
        loop["id"],
        to_state="review_ready",
        reason="Delivery approval was recreated from existing QA, gitleaks, and diff evidence.",
        actor="remediation",
        trigger="retry_delivery_approval",
        context_patch={"durableRun": review_durable},
        metadata={"remediationActionId": action["id"], "approvalJobId": approval_job["id"]},
        expected_version=loop["version"],
    )
    awaiting_durable = {
        **dict(review_ready["context"].get("durableRun") or {}),
        "status": "awaiting_approval",
        "approval": next_approval,
        "updatedAt": now,
    }
    awaiting = coordinator.transition_in_transaction(
        loop["id"],
        to_state="awaiting_approval",
        reason="Evidence-backed Product Loop result awaits operator approval.",
        actor="remediation",
        trigger="awaiting_approval",
        context_patch={"durableRun": awaiting_durable},
        metadata={"remediationActionId": action["id"], "approvalActionRequestId": approval_action["id"]},
        expected_version=review_ready["version"],
    )
    thread = threads.set_status(thread_id, "awaiting_approval")
    threads.record_event(
        thread_id=thread_id,
        type="approval_required",
        agent_role="aido_lead",
        payload={"loopId": awaiting["id"], **next_approval, "remediationActionId": action["id"]},
    )
    service.repository.resolve_pending_for_blocker_in_transaction(
        loop["id"],
        stage="approval",
        blocker_type="approval_unavailable",
    )
    return {
        "status": "awaiting_approval",
        "action": "retry_loop",
        "reason": "Recreated delivery approval from existing QA, gitleaks, and diff evidence.",
        "job": approval_job,
        "approval": next_approval,
        "actionRequest": approval_action,
        "loop": awaiting,
        "thread": thread,
        "retryOfLoopId": loop["id"],
        "threadId": thread_id,
    }


def blocked_gitleaks_context(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Contexto (coordinator, loop, durableRun, hilo) del loop bloqueado por gitleaks; ``None`` si no aplica."""
    loop_id = str(payload.get("loopId") or action.get("loopId") or "").strip()
    if not loop_id:
        return None
    coordinator = service._coordinator()
    try:
        loop = coordinator.get(loop_id)
    except KeyError:
        return None
    durable = dict((loop.get("context") or {}).get("durableRun") or {})
    if loop.get("state") != "blocked" or durable.get("blockedStage") != "gitleaks":
        return None
    thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
    thread_id = str(
        payload.get("threadId") or action.get("threadId") or thread_ref.get("projectThreadId") or ""
    ).strip()
    if not thread_id:
        return None
    return {
        "coordinator": coordinator,
        "loop": loop,
        "durable": durable,
        "threadId": thread_id,
        "threads": ThreadsRepository(service.connection),
    }


def retry_gitleaks_approval(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    coordinator: Any,
    loop: dict[str, Any],
    durable: dict[str, Any],
    thread_id: str,
    threads: ThreadsRepository,
    gitleaks: dict[str, Any],
) -> dict[str, Any]:
    """Reanuda la entrega de un loop bloqueado por gitleaks una vez que el escaneo quedó limpio."""
    from local_control_center.product_loop.coordinator import (
        _diff_ref_from_review,
        _diff_summary_from_review,
    )

    if not has_delivery_gitleaks_evidence(durable):
        return {
            "status": "blocked",
            "action": "run_gitleaks",
            "reason": (
                "Blocked gitleaks remediation is missing runtimeResult, teamSchedule, "
                "review diff, workspaceId, or workspacePath."
            ),
            "loopId": loop["id"],
        }
    thread = threads.get_thread(thread_id)
    if thread["projectId"] != loop["projectId"]:
        return {
            "status": "blocked",
            "action": "run_gitleaks",
            "reason": "Thread project does not match the blocked Product Loop project.",
            "loopId": loop["id"],
        }
    if thread["status"] in {"queued", "running"}:
        return {
            "status": "blocked",
            "action": "run_gitleaks",
            "reason": f"Thread is already {thread['status']}; gitleaks recovery would create concurrent delivery state.",
            "loopId": loop["id"],
        }

    team_schedule = durable["teamSchedule"]
    runtime_result = durable["runtimeResult"]
    review = durable["review"]
    workspace_id = str(durable.get("workspaceId") or "").strip()
    qa_results = runtime_result.get("qaResults") if isinstance(runtime_result.get("qaResults"), list) else []
    diff_ref = _diff_ref_from_review(review)
    diff_summary = _diff_summary_from_review(review)
    security_evidence = coordinator._record_run_evidence(
        project_id=loop["projectId"],
        loop_id=loop["id"],
        stage="gitleaks",
        status="completed",
        reason=str(gitleaks.get("reason") or "gitleaks passed."),
        details=gitleaks,
        workspace_id=workspace_id,
        diff_refs=[diff_ref],
        diff_summary=diff_summary,
        test_results=[
            *qa_results,
            {
                "command": "gitleaks",
                "status": "passed",
                "metadata": {
                    "workspaceId": workspace_id,
                    "findingCount": (gitleaks.get("gitleaks") or {}).get("findingCount", 0),
                },
            },
        ],
        tool_calls=gitleaks.get("toolCalls") or [],
        policy_decisions=[
            {"id": item} for item in (gitleaks.get("policyDecisionIds") or []) if str(item).strip()
        ],
    )
    try:
        resource_learning = coordinator._record_resource_learning(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=security_evidence["id"],
            success=True,
            rework=False,
            quality_score=1.0,
        )
    except Exception as error:
        return {
            "status": "blocked",
            "action": "run_gitleaks",
            "reason": f"AI resource learning persistence failed: {redact_secrets(str(error))}",
            "loopId": loop["id"],
            "evidencePackage": security_evidence,
        }

    evidence_refs = [str(item) for item in (durable.get("evidencePackageIds") or []) if str(item).strip()]
    if security_evidence["id"] not in evidence_refs:
        evidence_refs.append(security_evidence["id"])
    changed_files = [str(item) for item in (review.get("changedFiles") or []) if str(item).strip()]
    approval_payload = {
        "loopId": loop["id"],
        "workspaceId": workspace_id,
        "evidenceRefs": evidence_refs,
        "diffRefs": [diff_ref],
        "changedFiles": changed_files,
        "retryOfGitleaksLoopId": loop["id"],
        "remediationActionId": action["id"],
    }
    jobs = JobsRepository(service.connection)
    approval_job = jobs.create_job(
        project_id=loop["projectId"],
        kind="product_loop_delivery_approval",
        status="approval_required",
        payload=approval_payload,
    )["job"]
    approval_action = jobs.create_action_request(
        job_id=approval_job["id"],
        project_id=loop["projectId"],
        action_type="product_loop.approve_delivery",
        risk_level="medium",
        command="approve product loop delivery",
        payload=approval_payload,
        reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
    )
    next_approval = {
        "status": "available",
        "jobId": approval_job["id"],
        "actionRequestId": approval_action["id"],
        "evidenceRefs": evidence_refs,
        "diffRefs": approval_payload["diffRefs"],
    }
    now = utc_now()
    review_durable = {
        **durable,
        "status": "review_ready",
        "gitleaks": gitleaks,
        "resourceLearning": resource_learning,
        "approval": next_approval,
        "evidencePackageIds": evidence_refs,
        "updatedAt": now,
    }
    review_ready = coordinator.transition(
        loop["id"],
        to_state="review_ready",
        reason="gitleaks remediation passed and delivery evidence is ready for review.",
        actor="remediation",
        trigger="run_gitleaks",
        context_patch={"durableRun": review_durable},
        metadata={"remediationActionId": action["id"], "evidencePackageId": security_evidence["id"]},
    )
    awaiting_durable = {
        **dict(review_ready["context"].get("durableRun") or {}),
        "status": "awaiting_approval",
        "gitleaks": gitleaks,
        "resourceLearning": resource_learning,
        "approval": next_approval,
        "evidencePackageIds": evidence_refs,
        "updatedAt": now,
    }
    awaiting = coordinator.transition(
        loop["id"],
        to_state="awaiting_approval",
        reason="Evidence-backed Product Loop result awaits operator approval.",
        actor="remediation",
        trigger="awaiting_approval",
        context_patch={"durableRun": awaiting_durable},
        metadata={"remediationActionId": action["id"], "approvalActionRequestId": approval_action["id"]},
    )
    with suppress(Exception):
        threads.set_status(thread_id, "awaiting_approval")
    with suppress(Exception):
        threads.record_event(
            thread_id=thread_id,
            type="approval_required",
            agent_role="aido_lead",
            payload={
                "loopId": awaiting["id"],
                "resourceLearning": resource_learning,
                "evidencePackageId": security_evidence["id"],
                **next_approval,
                "remediationActionId": action["id"],
            },
        )
    return {
        "status": "awaiting_approval",
        "action": "run_gitleaks",
        "reason": "Recreated delivery approval after gitleaks remediation passed.",
        "resourceLearning": resource_learning,
        "evidencePackage": security_evidence,
        "job": approval_job,
        "approval": next_approval,
        "actionRequest": approval_action,
        "loop": awaiting,
        "thread": threads.get_thread(thread_id),
        "retryOfLoopId": loop["id"],
        "threadId": thread_id,
    }


def retry_resource_learning_approval(
    service: BlockerRemediationService,
    *,
    action: dict[str, Any],
    coordinator: Any,
    loop: dict[str, Any],
    durable: dict[str, Any],
    thread_id: str,
    threads: ThreadsRepository,
) -> dict[str, Any]:
    """Reanuda la entrega de un loop bloqueado por fallo al persistir el aprendizaje de recursos."""
    if not service.connection.in_transaction:
        raise RuntimeError("_retry_resource_learning_approval requires an active transaction.")
    if action.get("stage") != "resource_learning" or action.get("blockerType") != "resource_learning_failed":
        return {
            "status": "blocked",
            "action": "retry_loop",
            "reason": "Remediation action does not match the resource learning blocker.",
        }
    from local_control_center.product_loop.coordinator import _diff_ref_from_review

    resource_details = (
        durable.get("resource_learning") if isinstance(durable.get("resource_learning"), dict) else {}
    )
    failed_learning = (
        durable.get("resourceLearning") if isinstance(durable.get("resourceLearning"), dict) else {}
    )
    team_schedule = durable.get("teamSchedule") if isinstance(durable.get("teamSchedule"), dict) else None
    if team_schedule is None and isinstance(resource_details.get("teamSchedule"), dict):
        team_schedule = resource_details["teamSchedule"]
    runtime_result = durable.get("runtimeResult") if isinstance(durable.get("runtimeResult"), dict) else None
    review = durable.get("review") if isinstance(durable.get("review"), dict) else None
    if review is None and isinstance(resource_details.get("review"), dict):
        review = resource_details["review"]
    gitleaks = durable.get("gitleaks") if isinstance(durable.get("gitleaks"), dict) else None
    if gitleaks is None and isinstance(resource_details.get("gitleaks"), dict):
        gitleaks = resource_details["gitleaks"]
    evidence_ref = str(
        failed_learning.get("evidenceRef") or resource_details.get("evidenceRef") or ""
    ).strip()
    workspace_id = str(resource_details.get("workspaceId") or durable.get("workspaceId") or "").strip()
    changed_files = [str(item) for item in ((review or {}).get("changedFiles") or []) if str(item).strip()]
    if (
        team_schedule is None
        or runtime_result is None
        or review is None
        or not evidence_ref
        or not workspace_id
        or not changed_files
        or not isinstance(gitleaks, dict)
        or gitleaks.get("status") != "completed"
    ):
        return {
            "status": "blocked",
            "action": "retry_loop",
            "reason": (
                "Blocked resource learning retry is missing runtimeResult, teamSchedule, "
                "completed gitleaks, review diff, workspaceId, or evidenceRef."
            ),
            "loopId": loop["id"],
        }

    resource_learning = coordinator._record_resource_learning(
        project_id=loop["projectId"],
        loop_id=loop["id"],
        team_schedule=team_schedule,
        runtime_result=runtime_result,
        evidence_ref=evidence_ref,
        success=True,
        rework=False,
        quality_score=1.0,
    )
    evidence_refs = [str(item) for item in (durable.get("evidencePackageIds") or []) if str(item).strip()]
    if evidence_ref not in evidence_refs:
        evidence_refs.append(evidence_ref)
    approval_payload = {
        "loopId": loop["id"],
        "workspaceId": workspace_id,
        "evidenceRefs": evidence_refs,
        "diffRefs": [_diff_ref_from_review(review)],
        "changedFiles": changed_files,
        "retryOfResourceLearningLoopId": loop["id"],
        "remediationActionId": action["id"],
    }
    jobs = JobsRepository(service.connection)
    approval_job = jobs.create_job(
        project_id=loop["projectId"],
        kind="product_loop_delivery_approval",
        status="approval_required",
        payload=approval_payload,
    )["job"]
    approval_action = jobs.create_action_request(
        job_id=approval_job["id"],
        project_id=loop["projectId"],
        action_type="product_loop.approve_delivery",
        risk_level="medium",
        command="approve product loop delivery",
        payload=approval_payload,
        reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
    )
    next_approval = {
        "status": "available",
        "jobId": approval_job["id"],
        "actionRequestId": approval_action["id"],
        "evidenceRefs": evidence_refs,
        "diffRefs": approval_payload["diffRefs"],
    }
    now = utc_now()
    review_durable = {
        **durable,
        "status": "review_ready",
        "resourceLearning": resource_learning,
        "approval": next_approval,
        "updatedAt": now,
    }
    review_ready = coordinator.transition_in_transaction(
        loop["id"],
        to_state="review_ready",
        reason="AI resource learning was recorded from existing QA, gitleaks, and diff evidence.",
        actor="remediation",
        trigger="retry_resource_learning",
        context_patch={"durableRun": review_durable},
        metadata={"remediationActionId": action["id"], "approvalJobId": approval_job["id"]},
        expected_version=loop["version"],
    )
    awaiting_durable = {
        **dict(review_ready["context"].get("durableRun") or {}),
        "status": "awaiting_approval",
        "resourceLearning": resource_learning,
        "approval": next_approval,
        "updatedAt": now,
    }
    awaiting = coordinator.transition_in_transaction(
        loop["id"],
        to_state="awaiting_approval",
        reason="Evidence-backed Product Loop result awaits operator approval.",
        actor="remediation",
        trigger="awaiting_approval",
        context_patch={"durableRun": awaiting_durable},
        metadata={"remediationActionId": action["id"], "approvalActionRequestId": approval_action["id"]},
        expected_version=review_ready["version"],
    )
    thread = threads.set_status(thread_id, "awaiting_approval")
    threads.record_event(
        thread_id=thread_id,
        type="approval_required",
        agent_role="aido_lead",
        payload={
            "loopId": awaiting["id"],
            "resourceLearning": resource_learning,
            **next_approval,
            "remediationActionId": action["id"],
        },
    )
    service.repository.resolve_pending_for_blocker_in_transaction(
        loop["id"],
        stage="resource_learning",
        blocker_type="resource_learning_failed",
    )
    return {
        "status": "awaiting_approval",
        "action": "retry_loop",
        "reason": "Recorded AI resource learning and recreated delivery approval from existing evidence.",
        "resourceLearning": resource_learning,
        "job": approval_job,
        "approval": next_approval,
        "actionRequest": approval_action,
        "loop": awaiting,
        "thread": thread,
        "retryOfLoopId": loop["id"],
        "threadId": thread_id,
    }


def retry_root(service: BlockerRemediationService, project_id: str) -> str | None:
    """Raíz del proyecto para encolar el retry: la del servicio o la ruta persistida del proyecto."""
    if service.root is not None:
        return str(service.root)
    try:
        project = ProjectsRepository(service.connection).get_project(project_id)
    except KeyError:
        return None
    path = str(project.get("path") or "").strip()
    if not path:
        return None
    return str(Path(path).resolve(strict=False))


def continue_plan_only(service: BlockerRemediationService, *, action: dict[str, Any]) -> dict[str, Any]:
    """Continúa en modo plan-only el loop bloqueado de la acción, si la etapa aún coincide."""
    loop_id = str(action.get("loopId") or "").strip()
    if not loop_id:
        return {"status": "blocked", "action": "continue_plan_only", "reason": "loopId is required."}
    coordinator = service._coordinator()
    loop = coordinator.get(loop_id)
    if loop["state"] != "blocked":
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": "Product Loop is not blocked.",
        }

    durable = dict((loop.get("context") or {}).get("durableRun") or {})
    remediation_stage = str(action.get("stage") or "").strip()
    blocked_stage = str(durable.get("blockedStage") or "").strip()
    if remediation_stage and blocked_stage and remediation_stage != blocked_stage:
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": "Remediation stage no longer matches the current Product Loop blocker.",
            "remediationStage": remediation_stage,
            "blockedStage": blocked_stage,
        }
    thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
    request_meta = (
        strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta"))
        if isinstance(durable.get("requestMeta"), dict)
        else {}
    )
    thread_id = str(action.get("threadId") or thread_ref.get("projectThreadId") or "").strip()
    if not thread_id:
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": "Blocked Product Loop does not reference a project thread.",
        }

    threads = ThreadsRepository(service.connection)
    thread = threads.get_thread(thread_id)
    if thread["projectId"] != loop["projectId"]:
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": "Thread project does not match the blocked Product Loop project.",
        }
    if thread["status"] in {"queued", "running"}:
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": f"Thread is already {thread['status']}; plan-only retry would create concurrent execution.",
        }

    message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
    message = str(durable.get("message") or "").strip()
    source_message = source_retry_message(threads=threads, thread_id=thread_id, message_id=message_id)
    if source_message is not None:
        message_id = source_message["id"]
        message = message or str(source_message["content"] or "").strip()
    if not message_id or not message:
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": "Blocked Product Loop is missing the original thread message.",
        }

    root = retry_root(service, loop["projectId"])
    if not root:
        return {
            "status": "blocked",
            "action": "continue_plan_only",
            "reason": "Project root is required to queue a plan-only Product Loop retry.",
        }

    jobs = JobsRepository(service.connection)
    existing_job = existing_plan_only_job(
        jobs=jobs,
        project_id=loop["projectId"],
        loop_id=loop_id,
        action_id=action["id"],
    )
    if existing_job is None:
        queued_at = utc_now()
        approved_resource_selections = service._approved_resource_selections_from_durable(durable)
        run_metadata = {
            **request_meta,
            "messageId": message_id,
            "planOnly": True,
            "approvedResourceSelections": approved_resource_selections,
            "planOnlyOfLoopId": loop_id,
            "planOnlyStage": durable.get("blockedStage"),
            "planOnlyReason": durable.get("blockedReason"),
            "remediationActionId": action["id"],
            "planOnlyQueuedAt": queued_at,
        }
        created = jobs.create_job(
            project_id=loop["projectId"],
            kind="thread.product_loop.run",
            payload={
                "threadId": thread_id,
                "messageId": message_id,
                "projectId": loop["projectId"],
                "message": message,
                "title": thread["title"] or loop["title"],
                "root": root,
                "decision": request_meta.get("decision"),
                "teamPlan": request_meta.get("teamPlan"),
                "planOnly": True,
                "approvedResourceSelections": approved_resource_selections,
                "runMetadata": run_metadata,
                "planOnlyOfLoopId": loop_id,
                "planOnlyStage": durable.get("blockedStage"),
                "planOnlyReason": durable.get("blockedReason"),
                "remediationActionId": action["id"],
                "planOnlyQueuedAt": queued_at,
            },
            idempotency_key=f"product-loop-plan-only:{loop_id}:{action['id']}",
        )
        plan_job = created["job"]
    else:
        queued_at = utc_now()
        plan_job = existing_job

    plan_ref = {
        "status": "queued",
        "jobId": plan_job["id"],
        "threadId": thread_id,
        "messageId": message_id,
        "remediationActionId": action["id"],
        "queuedAt": queued_at,
    }
    durable = {
        **durable,
        "status": "plan_only_queued",
        "planOnly": True,
        "planOnlyRetry": plan_ref,
        "updatedAt": queued_at,
    }
    superseded = coordinator.transition(
        loop_id,
        to_state="cancelled",
        reason="Blocked Product Loop superseded by a queued plan-only job.",
        actor="remediation",
        trigger="continue_plan_only",
        context_patch={"durableRun": durable},
        metadata={"remediationActionId": action["id"], "planOnlyJobId": plan_job["id"]},
    )
    thread, thread_status_update = mark_thread_run_queued_best_effort(
        threads=threads,
        thread_id=thread_id,
        event_payload={
            "jobId": plan_job["id"],
            "messageId": message_id,
            "status": plan_job["status"],
            "planOnlyOfLoopId": loop_id,
            "remediationActionId": action["id"],
        },
    )
    return {
        "status": "queued",
        "action": "continue_plan_only",
        "reason": "Queued a plan-only Product Loop job.",
        "job": plan_job,
        "loop": superseded,
        "thread": thread,
        "threadStatusUpdate": thread_status_update,
        "planOnlyOfLoopId": loop_id,
        "threadId": thread_id,
        "messageId": message_id,
    }
