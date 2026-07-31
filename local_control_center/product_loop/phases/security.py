"""Fase de seguridad: gitleaks y SecurityAgent sobre la evidencia del run.

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

__all__ = ["run_security_phase"]


def run_security_phase(coordinator: ProductLoopCoordinator, run: _UserMessageRun) -> dict[str, Any] | None:
    """Corre los gates de seguridad (gitleaks + SecurityAgent) sobre la evidencia del run.

    Devuelve un dict terminal si un gate bloquea o falla; ``None`` deja el veredicto y el
    resumen de seguridad en ``run`` para la fase de aprobación.
    """
    from local_control_center.agents.security_agent import SecurityAgentRunner
    from local_control_center.project_constitution.prompt import render_constitution_prompt
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    thread_id = run.thread_id
    loop = run.loop
    git = run.git
    workspace = run.workspace
    runtime_result = run.runtime_result
    runtime_status = run.runtime_status
    review = run.review
    qa_results = run.qa_results
    agent_tasks = run.agent_tasks
    team_schedule = run.team_schedule
    loop = coordinator._transition_run_state(
        loop,
        to_state="security_running",
        reason="Running gitleaks delivery gate.",
        trigger="gitleaks",
        actor=actor,
        context_patch=coordinator._durable_run_patch(loop, {"status": "security_running"}),
        thread_id=thread_id,
    )
    try:
        try:
            gitleaks = git.gitleaks_scan(
                project_id,
                workspace_id=workspace["id"],
                workspace_path=workspace["path"],
            )
        except TypeError as error:
            if "unexpected keyword" not in str(error):
                raise
            gitleaks = git.gitleaks_scan(project_id)
    except Exception as error:
        reason = f"gitleaks delivery gate failed to run: {redact_secrets(str(error))}"
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="gitleaks",
            reason=reason,
            actor=actor,
            details={
                "status": "failed",
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
    if gitleaks.get("status") != "completed" or bool(gitleaks.get("deliveryBlocked")):
        reason = str(gitleaks.get("reason") or "gitleaks blocked delivery.")
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="gitleaks",
            reason=reason,
            actor=actor,
            details={
                **gitleaks,
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

    security_agent = run.security_runner or SecurityAgentRunner(
        coordinator.connection, root=run.effective_root
    )
    patch_artifact_id = str((runtime_result.get("diffSummary") or {}).get("patchArtifactId") or "")
    security_payload: dict[str, Any] = {
        "projectId": project_id,
        "workspaceId": workspace["id"],
        "taskId": f"{run.task_id}.security",
        "diffArtifactId": patch_artifact_id or None,
        "workflowRunId": loop["id"],
        "workflowStepId": "security_review",
    }
    story_specs_prompt = coordinator._story_specs_for_tasks(agent_tasks, for_role="security_engineer")
    if story_specs_prompt:
        security_payload["storySpecs"] = story_specs_prompt
    constitution_prompt = render_constitution_prompt(run.constitution)
    if constitution_prompt:
        security_payload["constitution"] = constitution_prompt
    security_resource = coordinator._security_execution_resource(team_schedule)
    if security_resource:
        security_payload["runModelAnalysis"] = True
        security_payload["preferredRuntime"] = security_resource["preferredRuntime"]
        if security_resource.get("model"):
            security_payload["model"] = security_resource["model"]
    try:
        security_result = security_agent.run(security_payload)
    except Exception as error:
        reason = f"SecurityAgent delivery gate failed to run: {redact_secrets(str(error))}"
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="security_agent",
            reason=reason,
            actor=actor,
            details={
                "status": "failed",
                "reason": reason,
                "workspaceId": workspace["id"],
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
    security_verdict = str(security_result.get("verdict") or "").strip().lower()
    security_summary = {
        "verdict": security_verdict,
        "reason": str(security_result.get("reason") or ""),
        "agentRunId": str((security_result.get("agentRun") or {}).get("id") or ""),
        "evidencePackageId": str((security_result.get("evidencePackage") or {}).get("id") or ""),
        "findingsArtifactId": str((security_result.get("findingsArtifact") or {}).get("id") or ""),
        "findingCount": len(security_result.get("findings") or []),
    }
    if security_verdict == "blocked":
        reason = str(security_result.get("reason") or "SecurityAgent blocked delivery.")
        return coordinator._block_after_runtime_with_resource_learning(
            loop,
            project_id=project_id,
            stage="security_agent",
            reason=reason,
            actor=actor,
            details={
                **security_summary,
                "status": "security_blocked",
                "reason": reason,
                "workspaceId": workspace["id"],
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
    run.gitleaks = gitleaks
    run.security_summary = security_summary
    return None
