"""Fases de ejecución: workspace del developer, corrida del runtime y captura de evidencia de review.

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

__all__ = ["capture_review_evidence", "execute_developer_phase", "prepare_developer_execution"]


def prepare_developer_execution(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Resuelve runtime y recurso del DeveloperAgent, asigna workspace y transiciona a executing.

    Devuelve el resultado terminal de un bloqueo de recurso/runtime/workspace o ``None``
    para continuar.
    """
    from local_control_center.agents.developer_agent import DeveloperAgentRunner
    from local_control_center.agents.developer_agent_contract import DEVELOPER_AGENT_ID
    from local_control_center.product_loop.repository import stable_task_suffix
    from local_control_center.shared.redaction import redact_secrets
    from local_control_center.shared.time import utc_now
    from local_control_center.workspaces_projects.repository import (
        WorkspaceConflictError,
        WorkspaceIsolationError,
        WorkspacesRepository,
    )

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread_id = run.thread_id
    effective_root = run.effective_root
    preferred_runtime = run.preferred_runtime
    runtime_runner = run.runtime_runner
    agent_tasks = run.agent_tasks
    team_schedule = run.team_schedule
    runtime = runtime_runner or DeveloperAgentRunner(coordinator.connection, root=effective_root)
    execution_resource = coordinator._developer_execution_resource(team_schedule)
    mapping_blockers = (
        coordinator._developer_execution_resource_mapping_blockers(team_schedule)
        if not execution_resource
        else []
    )
    if mapping_blockers:
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=(
                "AIResourceManager selected resources, but none of the execution roles maps to a "
                "DeveloperAgent runtime."
            ),
            actor=actor,
            details={
                "resourceBlockers": mapping_blockers,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            thread_id=thread_id,
        )
    resource_preferred_runtime = str(execution_resource.get("preferredRuntime") or "").strip() or None
    effective_preferred_runtime = resource_preferred_runtime or preferred_runtime
    try:
        readiness = runtime.status(preferred_runtime=effective_preferred_runtime)
    except Exception as error:
        reason = f"DeveloperAgent runtime readiness check failed: {redact_secrets(str(error))}"
        readiness = {
            "executable": False,
            "status": "failed",
            "selectedRuntimeId": effective_preferred_runtime,
            "reason": reason,
            "resourceSelection": execution_resource,
        }
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="runtime_selected",
        agent_role="developer",
        payload={
            "loopId": loop["id"],
            "runtimeId": readiness.get("selectedRuntimeId") or effective_preferred_runtime,
            "executable": bool(readiness.get("executable")),
            "reason": readiness.get("reason"),
            "resourceSelection": execution_resource,
        },
    )
    if not bool(readiness.get("executable")):
        reason = str(readiness.get("reason") or "No executable DeveloperAgent runtime is configured.")
        return coordinator._block_run(
            loop, stage="runtime", reason=reason, actor=actor, details=readiness, thread_id=thread_id
        )

    task_id = f"product-loop-{stable_task_suffix(thread_id, loop['id'])}"
    try:
        workspace = WorkspacesRepository(coordinator.connection, root=effective_root).allocate_workspace(
            project_id=project_id,
            task_id=task_id,
            agent_id=DEVELOPER_AGENT_ID,
            reason="ProductLoopCoordinator durable execution workspace",
            branch_name=(
                f"{coordinator._work_branch_prefix(project_id)}/product-loop-"
                f"{stable_task_suffix(thread_id, loop['id'])}"
            ),
            base_branch=coordinator._resolve_project_base_branch(project_id),
            reuse_existing=True,
        )
    except (WorkspaceConflictError, WorkspaceIsolationError, ValueError, KeyError) as error:
        return coordinator._block_run(
            loop,
            stage="workspace",
            reason=str(error),
            actor=actor,
            details={"taskId": task_id},
            thread_id=thread_id,
        )

    loop = coordinator._transition_run_state(
        loop,
        to_state="branch_ready",
        reason="Isolated workspace/worktree is ready for runtime execution.",
        trigger="branch_ready",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {
                "status": "branch_ready",
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "workspaceIsolationType": workspace["isolationType"],
            },
        ),
        thread_id=thread_id,
    )
    spec_render = coordinator._render_spec_artifacts(run, workspace)
    if spec_render:
        loop = coordinator.repository.update_loop_context(
            loop["id"],
            context={
                **loop["context"],
                "durableRun": {
                    **coordinator._durable_run_context(loop),
                    "specArtifacts": spec_render,
                    "updatedAt": utc_now(),
                },
            },
        )
    loop = coordinator._transition_run_state(
        loop,
        to_state="executing",
        reason="Executing DeveloperAgent runtime in the isolated workspace.",
        trigger="runtime_execution",
        actor=actor,
        context_patch=coordinator._durable_run_patch(loop, {"status": "executing"}),
        thread_id=thread_id,
    )
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="agent_running",
        agent_role="developer",
        payload={
            "loopId": loop["id"],
            "agentId": DEVELOPER_AGENT_ID,
            "role": "developer",
            "runtimeId": readiness.get("selectedRuntimeId") or effective_preferred_runtime,
            "workspaceId": workspace["id"],
            "assignmentId": task_id,
            "resourceSelection": execution_resource,
        },
    )
    run.loop = loop
    run.runtime = runtime
    run.execution_resource = execution_resource
    run.effective_preferred_runtime = effective_preferred_runtime
    run.readiness = readiness
    run.task_id = task_id
    run.workspace = workspace
    return None


def execute_developer_phase(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Construye el payload del DeveloperAgent y lo ejecuta en el workspace aislado.

    Devuelve el resultado terminal (con learning de recursos) si el runtime falla, o
    ``None`` para continuar con la captura de evidencia.
    """
    from local_control_center.product_loop.coordinator import _bounded_instruction
    from local_control_center.project_constitution.prompt import render_constitution_prompt
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread = run.thread
    thread_id = run.thread_id
    message_text = run.message_text
    qa_commands = run.qa_commands
    runtime = run.runtime
    readiness = run.readiness
    task_id = run.task_id
    workspace = run.workspace
    execution_resource = run.execution_resource
    effective_preferred_runtime = run.effective_preferred_runtime
    agent_tasks = run.agent_tasks
    team_schedule = run.team_schedule
    team_assignments = run.team_assignments
    product_owner_output_record = run.product_owner_output_record
    backlog_artifact = run.backlog_artifact
    instruction = _bounded_instruction(message_text)
    if run.rework_feedback:
        instruction = (
            f"{instruction}\n\n[QA rework feedback - round {run.rework_round}]\n{run.rework_feedback}"
        )
    developer_payload = {
        "projectId": project_id,
        "workspaceId": workspace["id"],
        "taskId": task_id,
        "instruction": instruction,
        "storySpecs": coordinator._story_specs_for_tasks(agent_tasks),
        "agentTasks": agent_tasks,
        "teamSchedule": team_schedule,
        "agentAssignments": team_assignments,
        "productOwnerOutputId": product_owner_output_record["id"],
        "backlogArtifactId": backlog_artifact["id"],
        "preferredRuntime": effective_preferred_runtime,
        "qaCommands": qa_commands or [],
        "requireApproval": True,
        "resourceSelection": execution_resource,
        "metadata": {"loopId": loop["id"], "projectThreadId": thread.get("id")},
    }
    constitution_prompt = render_constitution_prompt(run.constitution)
    if constitution_prompt:
        developer_payload["constitution"] = constitution_prompt
    if run.rework_round:
        developer_payload["reworkRound"] = run.rework_round
    if (
        execution_resource.get("model")
        and execution_resource.get("preferredRuntime") == effective_preferred_runtime
    ):
        developer_payload["model"] = execution_resource["model"]
    failover_attempts: list[dict[str, Any]] = []
    try:
        runtime_result = coordinator._run_with_failover(
            runtime=runtime,
            payload=developer_payload,
            run=run,
            attempts=failover_attempts,
            thread_id=thread_id,
        )
    except Exception as error:
        reason = str(redact_secrets(str(error)))
        if failover_attempts:
            reason = f"runtime_failover_exhausted: {reason}"
        blocked_result = coordinator._block_run(
            loop,
            stage="runtime",
            reason=reason,
            actor=actor,
            details={
                "status": "failed",
                "reason": reason,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "runtimeStatus": "failed",
                "runtime": readiness,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
                "failoverAttempts": failover_attempts,
            },
            thread_id=thread_id,
        )
        evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
        if evidence_ref:
            resource_learning = coordinator._record_resource_learning_best_effort(
                project_id=project_id,
                loop_id=loop["id"],
                team_schedule=team_schedule,
                runtime_result={
                    "status": "failed",
                    "reason": reason,
                    "runtime": readiness,
                    "workspaceId": workspace["id"],
                },
                evidence_ref=evidence_ref,
                success=False,
                rework=False,
                quality_score=0.0,
            )
            blocked_result = coordinator._attach_resource_learning_to_result(
                blocked_result,
                resource_learning,
            )
        return blocked_result
    run.runtime_result = runtime_result
    return None


def capture_review_evidence(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Captura el diff real del worktree como evidencia de review del runtime.

    Devuelve el resultado terminal (con learning) si el diff no se puede capturar o no
    hay archivos cambiados, o ``None`` para continuar al gate de QA.
    """
    from local_control_center.product_loop.coordinator import (
        _review_from_diff,
        _review_from_runtime,
        capture_git_diff,
    )
    from local_control_center.shared.redaction import redact_secrets
    from local_control_center.workspaces_projects.git_worktrees import (
        commit_workspace_changes,
    )

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread_id = run.thread_id
    effective_root = run.effective_root
    task_id = run.task_id
    workspace = run.workspace
    runtime_result = run.runtime_result
    agent_tasks = run.agent_tasks
    team_schedule = run.team_schedule
    runtime_status = str(runtime_result.get("status") or "failed")
    evidence_ids = coordinator._external_evidence_ids(runtime_result)
    review = _review_from_runtime(runtime_result)
    review_capture_reason: str | None = None
    if workspace["isolationType"] == "git_worktree":
        try:
            diff = capture_git_diff(
                Path(workspace["path"]),
                connection=coordinator.connection,
                root=effective_root,
                project_id=project_id,
                workspace_id=workspace["id"],
                task_id=f"{task_id}.review_diff",
            )
            review = _review_from_diff(diff)
        except Exception as error:
            review_capture_reason = (
                "Product Loop review diff capture failed from the assigned git worktree: "
                f"{redact_secrets(str(error))}"
            )
            review = {
                "state": "capture_failed",
                "changedFiles": [],
                "branch": None,
                "headCommit": None,
                "diffStat": "",
                "patch": "",
                "patchSizeBytes": 0,
                "truncated": False,
                "toolCalls": [],
                "policyDecisionIds": [],
            }
        if review["state"] != "captured":
            reason = (
                review_capture_reason
                or "Product Loop review diff could not be captured from the assigned git worktree."
            )
            blocked_result = coordinator._block_run(
                loop,
                stage="review",
                reason=reason,
                actor=actor,
                details={
                    "status": str(review.get("state") or "diff_unavailable"),
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "runtimeResult": runtime_result,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
            evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
            if evidence_ref:
                resource_learning = coordinator._record_resource_learning_best_effort(
                    project_id=project_id,
                    loop_id=loop["id"],
                    team_schedule=team_schedule,
                    runtime_result=runtime_result,
                    evidence_ref=evidence_ref,
                    success=False,
                    rework=True,
                    quality_score=0.0,
                )
                blocked_result = coordinator._attach_resource_learning_to_result(
                    blocked_result,
                    resource_learning,
                )
            return blocked_result
    if not review["changedFiles"]:
        reason = (
            "Product Loop runtime completed without real changed files in the assigned worktree."
            if workspace["isolationType"] == "git_worktree"
            else "Product Loop runtime completed without changed files evidence."
        )
        blocked_result = coordinator._block_run(
            loop,
            stage="review",
            reason=reason,
            actor=actor,
            details={
                "status": str(review.get("state") or "diff_unavailable"),
                "reason": reason,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "runtimeStatus": runtime_status,
                "runtimeResult": runtime_result,
                "review": review,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            thread_id=thread_id,
        )
        evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
        if evidence_ref:
            resource_learning = coordinator._record_resource_learning_best_effort(
                project_id=project_id,
                loop_id=loop["id"],
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                evidence_ref=evidence_ref,
                success=False,
                rework=True,
                quality_score=0.0,
            )
            blocked_result = coordinator._attach_resource_learning_to_result(
                blocked_result,
                resource_learning,
            )
        return blocked_result
    if workspace["isolationType"] == "git_worktree" and review.get("changedFiles"):
        # El trabajo capturado como evidencia se persiste como commit real en la rama de la HU,
        # para que el aterrizaje (merge/PR) tenga commits y no solo un patch. Best-effort: si el
        # commit falla, el loop continúa con la evidencia ya capturada como hasta ahora.
        try:
            commit_result = commit_workspace_changes(
                workspace_path=Path(workspace["path"]),
                message=f"AIDO product loop iteration: {task_id}",
                connection=coordinator.connection,
                root=effective_root,
                project_id=project_id,
                workspace_id=workspace["id"],
            )
        except Exception as error:
            # Señal auxiliar: un fallo de commit nunca debe tumbar el loop; se registra y sigue.
            commit_result = {"status": "commit_failed", "reason": redact_secrets(str(error))}
        review = {**review, "commit": commit_result}
    run.runtime_status = runtime_status
    run.evidence_ids = evidence_ids
    run.review = review
    return None
