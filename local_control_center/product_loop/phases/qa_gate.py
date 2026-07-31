"""Fase de gate QA: evalúa el veredicto del runtime y decide rework acotado, bloqueo o avance.

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

__all__ = ["evaluate_qa_gate"]


def evaluate_qa_gate(coordinator: ProductLoopCoordinator, run: _UserMessageRun) -> dict[str, Any] | None:
    """Evalúa el veredicto QA del runtime y decide rework, bloqueo o avance a Security.

    Devuelve un dict terminal (bloqueo o rondas de rework agotadas) o ``None`` para continuar;
    cuando QA falla dentro del presupuesto de rondas deja ``run.should_rework`` en ``True`` para
    que el driver re-ejecute al developer con el feedback compactado.
    """
    from local_control_center.product_loop.coordinator import (
        DEFAULT_AUTO_REWORK_ROUNDS,
        REWORK_STATE,
        ProductLoopStopConditionError,
    )
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    thread_id = run.thread_id
    loop = run.loop
    workspace = run.workspace
    runtime_result = run.runtime_result
    runtime_status = run.runtime_status
    review = run.review
    evidence_ids = run.evidence_ids
    agent_tasks = run.agent_tasks
    team_schedule = run.team_schedule
    run.should_rework = False

    loop = coordinator._transition_run_state(
        loop,
        to_state="qa_running",
        reason="Runtime finished; QA evidence is being evaluated.",
        trigger="qa_running",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {"status": "qa_running", "runtimeResult": runtime_result, "review": review},
            evidence_package_ids=evidence_ids,
        ),
        thread_id=thread_id,
    )
    run.loop = loop
    qa_results = runtime_result.get("qaResults") if isinstance(runtime_result.get("qaResults"), list) else []
    qa_verdict = str((runtime_result.get("evidencePackage") or {}).get("qaVerdict") or "").lower()
    qa_statuses = [
        str(result.get("status") or "").strip().lower() for result in qa_results if isinstance(result, dict)
    ]
    qa_block_details = {
        **runtime_result,
        "status": "qa_blocked",
        "workspaceId": workspace["id"],
        "workspacePath": workspace["path"],
        "runtimeStatus": runtime_status,
        "qaVerdict": qa_verdict,
        "qaResults": qa_results,
        "review": review,
        "teamSchedule": team_schedule,
        "agentTaskIds": [task["id"] for task in agent_tasks],
    }
    if runtime_status == "qa_failed" or qa_verdict == "failed" or "failed" in qa_statuses:
        reason = str(runtime_result.get("reason") or "QA failed and requires rework.")
        evidence = coordinator._record_run_evidence(
            project_id=project_id,
            loop_id=loop["id"],
            stage="qa",
            status="reworking",
            reason=reason,
            details={**runtime_result, "reworkRound": run.rework_round},
        )
        try:
            resource_learning = coordinator._record_resource_learning(
                project_id=project_id,
                loop_id=loop["id"],
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                evidence_ref=evidence["id"],
                success=False,
                rework=True,
                quality_score=0.0,
            )
        except Exception as error:
            learning_reason = f"AI resource learning persistence failed: {redact_secrets(str(error))}"
            return coordinator._block_run(
                loop,
                stage="resource_learning",
                reason=learning_reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "reason": learning_reason,
                    "evidenceRef": evidence["id"],
                    "runtimeStatus": runtime_status,
                    "qaVerdict": qa_verdict,
                    "qaResults": qa_results,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                durable_context={
                    "rework": {"source": "qa", "reason": reason, "runtimeStatus": runtime_status},
                    "resourceLearning": {
                        "status": "persistence_failed",
                        "reason": learning_reason,
                        "evidenceRef": evidence["id"],
                    },
                },
                thread_id=thread_id,
            )
        try:
            reworked = coordinator._transition_run_state(
                loop,
                to_state=REWORK_STATE,
                reason=reason,
                trigger="qa_failed",
                actor=actor,
                context_patch=coordinator._durable_run_patch(
                    loop,
                    {
                        "status": REWORK_STATE,
                        "rework": {
                            "source": "qa",
                            "reason": reason,
                            "runtimeStatus": runtime_status,
                            "reworkRound": run.rework_round,
                        },
                        "resourceLearning": resource_learning,
                    },
                    evidence_package_ids=[*evidence_ids, evidence["id"]],
                ),
                metadata={"evidencePackageId": evidence["id"]},
                thread_id=thread_id,
            )
        except ProductLoopStopConditionError as stop_error:
            return coordinator._block_run(
                loop,
                stage="qa_rework",
                reason=str(stop_error),
                actor=actor,
                details={
                    "status": "stop_condition",
                    "reason": str(stop_error),
                    "runtimeStatus": runtime_status,
                    "qaVerdict": qa_verdict,
                    "reworkRound": run.rework_round,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
        run.loop = reworked
        if run.rework_round >= DEFAULT_AUTO_REWORK_ROUNDS:
            return coordinator._run_result(
                reworked, status=REWORK_STATE, reason=reason, evidence_package=evidence
            )
        run.rework_round += 1
        run.rework_feedback = coordinator._qa_rework_feedback(qa_results)
        run.task_id = f"{run.base_task_id}:r{run.rework_round}"
        loop = coordinator._transition_run_state(
            reworked,
            to_state="executing",
            reason=(f"Auto rework round {run.rework_round}: re-executing DeveloperAgent with QA feedback."),
            trigger="auto_rework",
            actor=actor,
            context_patch=coordinator._durable_run_patch(
                reworked,
                {
                    "status": "executing",
                    "autoRework": {"round": run.rework_round, "reason": reason},
                },
            ),
            thread_id=thread_id,
        )
        run.loop = loop
        run.should_rework = True
        return None
    if not qa_results:
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="qa",
            reason="QA results are required before Product Loop delivery approval.",
            actor=actor,
            details={
                **qa_block_details,
                "reason": "QA results are required before Product Loop delivery approval.",
            },
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            thread_id=thread_id,
        )
    non_passing_qa = [
        result
        for result in qa_results
        if not isinstance(result, dict) or str(result.get("status") or "").strip().lower() != "passed"
    ]
    if non_passing_qa:
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="qa",
            reason="QA results must all pass before Product Loop delivery approval.",
            actor=actor,
            details={
                **qa_block_details,
                "reason": "QA results must all pass before Product Loop delivery approval.",
                "nonPassingQaResults": non_passing_qa,
            },
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            thread_id=thread_id,
        )
    if qa_verdict in {"blocked", "not_started"}:
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="qa",
            reason=f"QA verdict is {qa_verdict}; Product Loop delivery approval requires passing QA evidence.",
            actor=actor,
            details={
                **qa_block_details,
                "reason": (
                    f"QA verdict is {qa_verdict}; Product Loop delivery approval requires "
                    "passing QA evidence."
                ),
            },
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            thread_id=thread_id,
        )
    if runtime_status not in {"completed", "evidence_ready"}:
        reason = str(runtime_result.get("reason") or f"Runtime ended with status {runtime_status}.")
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="runtime",
            reason=reason,
            actor=actor,
            details={
                **runtime_result,
                "reason": reason,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "runtimeStatus": runtime_status,
                "qaResults": qa_results,
                "review": review,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            thread_id=thread_id,
        )
    run.loop = loop
    run.qa_results = qa_results
    run.qa_verdict = qa_verdict
    return None
