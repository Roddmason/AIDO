"""Fase de aprobación: evidencia consolidada, job/ActionRequest y estacionamiento del loop.

Fase del pipeline spec-driven extraída de ``coordinator.py``: cada función recibe el coordinator
(driver del FSM y dueño de la transacción) y el ``run`` mutable, y devuelve un dict terminal o
``None`` para continuar. Los helpers y constantes compartidos se importan de forma diferida desde
el coordinator para evitar el ciclo de imports (el coordinator importa este módulo al cargar).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun

__all__ = ["finalize_delivery_approval"]


def finalize_delivery_approval(coordinator: ProductLoopCoordinator, run: _UserMessageRun) -> dict[str, Any]:
    """Registra la evidencia de seguridad, crea la aprobación de entrega y estaciona el loop.

    Cierra el camino feliz: evidencia consolidada, job + ActionRequest de aprobación, y las
    transiciones ``review_ready`` → ``awaiting_approval`` con su evento de hilo.
    """
    from local_control_center.product_loop.coordinator import (
        _diff_ref_from_review,
        _diff_summary_from_review,
    )
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    thread_id = run.thread_id
    loop = run.loop
    workspace = run.workspace
    runtime_result = run.runtime_result
    review = run.review
    qa_results = run.qa_results
    evidence_ids = run.evidence_ids
    agent_tasks = run.agent_tasks
    team_schedule = run.team_schedule
    gitleaks = run.gitleaks
    security_summary = run.security_summary
    diff_ref = _diff_ref_from_review(review)
    diff_summary = _diff_summary_from_review(review)
    security_evidence = coordinator._record_run_evidence(
        project_id=project_id,
        loop_id=loop["id"],
        stage="gitleaks",
        status="completed",
        reason=str(gitleaks.get("reason") or "gitleaks passed."),
        details={**gitleaks, "securityAgent": security_summary},
        workspace_id=workspace["id"],
        diff_refs=[diff_ref],
        diff_summary=diff_summary,
        test_results=[
            *qa_results,
            {
                "command": "gitleaks",
                "status": "passed",
                "metadata": {
                    "workspaceId": workspace["id"],
                    "findingCount": (gitleaks.get("gitleaks") or {}).get("findingCount", 0),
                },
            },
            {
                "command": "security_agent",
                "status": "passed",
                "metadata": {"workspaceId": workspace["id"], **security_summary},
            },
        ],
        tool_calls=gitleaks.get("toolCalls") or [],
        policy_decisions=[
            {"id": item} for item in (gitleaks.get("policyDecisionIds") or []) if str(item).strip()
        ],
    )
    try:
        resource_learning = coordinator._record_resource_learning(
            project_id=project_id,
            loop_id=loop["id"],
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=security_evidence["id"],
            success=True,
            rework=False,
            quality_score=1.0,
        )
    except Exception as error:
        reason = f"AI resource learning persistence failed: {redact_secrets(str(error))}"
        return coordinator._block_run(
            loop,
            stage="resource_learning",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "reason": reason,
                "workspaceId": workspace["id"],
                "evidenceRef": security_evidence["id"],
                "review": review,
                "gitleaks": gitleaks,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            durable_context={
                "review": review,
                "gitleaks": gitleaks,
                "resourceLearning": {
                    "status": "persistence_failed",
                    "reason": reason,
                    "evidenceRef": security_evidence["id"],
                },
            },
            thread_id=thread_id,
        )
    approval_evidence_ids = [*evidence_ids, security_evidence["id"]]
    approval_payload = {
        "loopId": loop["id"],
        "workspaceId": workspace["id"],
        "evidenceRefs": approval_evidence_ids,
        "diffRefs": [diff_ref],
        "changedFiles": review["changedFiles"],
    }
    try:
        approval_job = coordinator.jobs.create_job(
            project_id=project_id,
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload=approval_payload,
        )["job"]
        approval_action = coordinator.jobs.create_action_request(
            job_id=approval_job["id"],
            project_id=project_id,
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload=approval_payload,
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
    except Exception as error:
        reason = f"Product Loop delivery approval persistence failed: {redact_secrets(str(error))}"
        return coordinator._block_run(
            loop,
            stage="approval",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "reason": reason,
                "review": review,
                "gitleaks": gitleaks,
                "resourceLearning": resource_learning,
                **approval_payload,
            },
            durable_context={
                "review": review,
                "gitleaks": gitleaks,
                "resourceLearning": resource_learning,
                "approval": {
                    "status": "unavailable",
                    "reason": reason,
                    **approval_payload,
                },
            },
            thread_id=thread_id,
        )
    approval = {
        "status": "available",
        "jobId": approval_job["id"],
        "actionRequestId": approval_action["id"],
        "evidenceRefs": approval_evidence_ids,
        "diffRefs": [diff_ref],
    }
    loop = coordinator._transition_run_state(
        loop,
        to_state="review_ready",
        reason="Runtime, QA and gitleaks evidence are ready for review.",
        trigger="review_ready",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {
                "status": "review_ready",
                "gitleaks": gitleaks,
                "review": review,
                "approval": approval,
                "resourceLearning": resource_learning,
            },
            evidence_package_ids=[security_evidence["id"]],
        ),
        thread_id=thread_id,
    )
    loop = coordinator._transition_run_state(
        loop,
        to_state="awaiting_approval",
        reason="Evidence-backed Product Loop result awaits operator approval.",
        trigger="awaiting_approval",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop, {"status": "awaiting_approval", "approval": approval}
        ),
        thread_id=thread_id,
    )
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="approval_required",
        agent_role="aido_lead",
        payload={"loopId": loop["id"], **approval},
    )
    return coordinator._run_result(
        loop,
        status="awaiting_approval",
        reason="Evidence-backed Product Loop result awaits operator approval.",
        evidence_package=security_evidence,
    )
