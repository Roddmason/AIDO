"""Fases de ejecución: workspace del developer, corrida del runtime y captura de evidencia de review.

Fase del pipeline spec-driven extraída de ``coordinator.py``: cada función recibe el coordinator
(driver del FSM y dueño de la transacción) y el ``run`` mutable, y devuelve un dict terminal o
``None`` para continuar. Los helpers y constantes compartidos se importan de forma diferida desde
el coordinator para evitar el ciclo de imports (el coordinator importa este módulo al cargar).

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun

__all__ = [
    "aggregate_story_reviews",
    "block_without_changed_files",
    "capture_cumulative_review",
    "capture_review_evidence",
    "execute_developer_phase",
    "prepare_developer_execution",
]

COMMIT_FAILURE_DETAIL_CHARS = 500
"""Tope del detalle (redactado) de un commit fallido dentro de la razón de bloqueo persistida."""


def _block_interrupted_execution(coordinator, run, *, reason, runtime_result=None):
    from local_control_center.process_supervision.context import CURRENT_EXECUTION
    from local_control_center.process_supervision.repository import ManagedProcessRepository
    from local_control_center.product_loop.coordinator import _ProductLoopCancelled

    context = CURRENT_EXECUTION.get()
    if (
        context is None
        or context.connection is not coordinator.connection
        or not context.in_job_runner
        or context.project_id != run.project_id
        or not context.execution_id
    ):
        raise ValueError("Interrupted execution requires its trusted worker identity.")
    job = coordinator.connection.execute(
        "SELECT status FROM jobs WHERE id=?", (context.execution_id,)
    ).fetchone()
    if (job and job["status"] == "cancelled") or ManagedProcessRepository(
        coordinator.connection
    ).cancellation_reason(context.execution_id):
        raise _ProductLoopCancelled(run.loop["id"], run.thread_id)
    return coordinator._block_run(
        run.loop,
        stage="worker",
        reason=reason,
        actor=run.actor,
        thread_id=run.thread_id,
        details={
            "status": "failed",
            "reason": "execution_deadline_exhausted",
            "interruptedExecutionId": context.execution_id,
            "workspaceId": run.workspace["id"],
            "workspacePath": run.workspace["path"],
            "runtimeResult": runtime_result or {},
            "partialEvidence": True,
        },
    )


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
    execution_resource = coordinator._developer_execution_resource(team_schedule, run.request_meta)
    assignment_blocker = coordinator._developer_assignment_blocker(run.request_meta, execution_resource)
    if assignment_blocker is not None:
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=assignment_blocker["reason"],
            actor=actor,
            details={
                "resourceBlockers": [assignment_blocker],
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            thread_id=thread_id,
        )
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
    from local_control_center.process_supervision.context import ExecutionDeadlineExceeded
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
    agent_tasks = run.active_story_tasks if run.active_story_tasks is not None else run.agent_tasks
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
            role="developer",
        )
    except ExecutionDeadlineExceeded as error:
        return _block_interrupted_execution(coordinator, run, reason=str(error))
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
    execution_result = runtime_result.get("runtimeResult") or {}
    if execution_result.get("timedOut") is True:
        return _block_interrupted_execution(
            coordinator,
            run,
            reason="execution_deadline_exhausted: the runtime timed out; inspect the preserved partial workspace changes.",
            runtime_result=runtime_result,
        )
    run.runtime_result = runtime_result
    return None


def capture_review_evidence(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Captura el diff real del worktree como evidencia de review del runtime.

    Devuelve el resultado terminal (con learning) si el diff no se puede capturar o no
    hay archivos cambiados, o ``None`` para continuar al gate de QA. El trabajo capturado se
    persiste como commit en la rama del hilo: en un run por historia el commit es obligatorio (el
    diff acumulado de Security y la aprobación lo necesita) y su fallo bloquea en ``review``; en un
    run único sigue siendo best-effort. Una historia sin cambios es ``noop`` (sin QA) solo en su
    primer intento y sin señales de QA fallido; un rework sin cambios o con QA en rojo sigue al gate
    de QA, que evalúa el estado vigente del código ya commiteado.
    """
    from local_control_center.product_loop.coordinator import (
        _review_from_diff,
        _review_from_runtime,
        capture_git_diff,
    )
    from local_control_center.product_loop.phases.qa_gate import qa_evidence_passes
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
        # Un rework no repite el cambio ya commiteado de la historia: si QA sigue fallando, decide
        # el gate de QA (rondas y causa real), no el guard de "sin trabajo".
        rework_still_failing_qa = run.rework_round > 0 and runtime_status == "qa_failed"
        if run.active_story_tasks is not None and (
            runtime_status in {"completed", "evidence_ready"} or rework_still_failing_qa
        ):
            run.runtime_status = runtime_status
            run.evidence_ids = evidence_ids
            run.review = review
            run.story_noop = run.rework_round == 0 and qa_evidence_passes(runtime_result)
            return None
        return block_without_changed_files(coordinator, run, review, runtime_status=runtime_status)
    if workspace["isolationType"] == "git_worktree" and review.get("changedFiles"):
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
            commit_result = {"status": "commit_failed", "reason": redact_secrets(str(error))}
        review = {**review, "commit": commit_result}
        if run.active_story_tasks is not None and commit_result.get("status") != "committed":
            commit_output = str(
                commit_result.get("stderr") or commit_result.get("reason") or commit_result.get("status")
            )
            reason = (
                "Product Loop could not commit the story changes to the thread branch, so the "
                "cumulative diff for Security and approval would be incomplete: "
                f"{str(redact_secrets(commit_output))[:COMMIT_FAILURE_DETAIL_CHARS]}"
            )
            return coordinator._block_run(
                loop,
                stage="review",
                reason=reason,
                actor=actor,
                details={
                    "status": str(commit_result.get("status") or "commit_failed"),
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
    run.runtime_status = runtime_status
    run.evidence_ids = evidence_ids
    run.review = review
    return None


def block_without_changed_files(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    review: dict[str, Any],
    *,
    runtime_status: str,
) -> dict[str, Any]:
    """Bloquea el run en ``review`` cuando no hay archivos cambiados reales y registra el learning.

    Guard fail-closed de "el runtime terminó sin trabajo": lo usan la captura por historia (runtime
    no sano sin cambios) y el cierre acumulado (ninguna historia del run dejó cambios).
    """
    workspace = run.workspace
    reason = (
        "Product Loop runtime completed without real changed files in the assigned worktree."
        if workspace["isolationType"] == "git_worktree"
        else "Product Loop runtime completed without changed files evidence."
    )
    blocked_result = coordinator._block_run(
        run.loop,
        stage="review",
        reason=reason,
        actor=run.actor,
        details={
            "status": str(review.get("state") or "diff_unavailable"),
            "reason": reason,
            "workspaceId": workspace["id"],
            "workspacePath": workspace["path"],
            "runtimeStatus": runtime_status,
            "runtimeResult": run.runtime_result,
            "review": review,
            "teamSchedule": run.team_schedule,
            "agentTaskIds": [task["id"] for task in run.agent_tasks],
        },
        thread_id=run.thread_id,
    )
    evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
    if evidence_ref:
        resource_learning = coordinator._record_resource_learning_best_effort(
            project_id=run.project_id,
            loop_id=run.loop["id"],
            team_schedule=run.team_schedule,
            runtime_result=run.runtime_result,
            evidence_ref=evidence_ref,
            success=False,
            rework=True,
            quality_score=0.0,
        )
        blocked_result = coordinator._attach_resource_learning_to_result(blocked_result, resource_learning)
    return blocked_result


def aggregate_story_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Une las reviews por historia del run en una vista acumulada (archivos únicos en orden)."""
    changed: list[str] = []
    patches: list[str] = []
    stats: list[str] = []
    tool_calls: list[Any] = []
    policy_ids: list[Any] = []
    for review in reviews:
        for path in review.get("changedFiles") or []:
            if path not in changed:
                changed.append(path)
        if review.get("patch"):
            patches.append(str(review["patch"]))
        if review.get("diffStat"):
            stats.append(str(review["diffStat"]))
        tool_calls.extend(review.get("toolCalls") or [])
        policy_ids.extend(review.get("policyDecisionIds") or [])
    last = reviews[-1] if reviews else {}
    patch = "\n".join(patches)
    return {
        "state": last.get("state") or "runtime_reported",
        "changedFiles": changed,
        "branch": last.get("branch"),
        "headCommit": last.get("headCommit"),
        "diffStat": "\n".join(stats)[:4000],
        "patch": patch[:12000],
        "patchSizeBytes": sum(int(review.get("patchSizeBytes") or 0) for review in reviews),
        "truncated": len(patch) > 12000 or any(bool(review.get("truncated")) for review in reviews),
        "toolCalls": tool_calls,
        "policyDecisionIds": policy_ids,
    }


def capture_cumulative_review(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Consolida la evidencia de todas las historias del run antes de Security y la aprobación.

    En un worktree git captura el diff acumulado ``<base>...HEAD`` de la rama del hilo (base
    configurada o, como respaldo, el commit desde el que se creó el worktree) y lo guarda como
    artefacto ``git_patch`` para el SecurityAgent; fuera de git agrega las reviews por historia.
    Devuelve el bloqueo ``review`` si la captura falla, si queda trabajo sin commitear fuera de
    ``.aido/`` (el rango ``<base>...HEAD`` no lo vería y Security revisaría un diff incompleto) o si
    ninguna historia dejó cambios; si no, deja la vista acumulada en ``run.review``.
    """
    from local_control_center.product_loop.coordinator import _review_from_diff
    from local_control_center.product_loop.spec_artifacts import exclude_aido_artifacts
    from local_control_center.shared.redaction import redact_secrets
    from local_control_center.workspaces_projects.git_worktrees import capture_cumulative_diff

    workspace = run.workspace
    if workspace["isolationType"] != "git_worktree":
        review = aggregate_story_reviews(run.story_reviews)
    else:
        worktree = (workspace.get("metadata") or {}).get("gitWorktree") or {}
        base_refs = [
            coordinator._resolve_project_base_branch(run.project_id),
            str(worktree.get("sourceCommit") or ""),
        ]
        try:
            diff = capture_cumulative_diff(
                Path(workspace["path"]),
                base_refs=base_refs,
                connection=coordinator.connection,
                root=run.effective_root,
                project_id=run.project_id,
                workspace_id=workspace["id"],
                task_id=f"{run.task_id}.cumulative_diff",
            )
        except Exception as error:
            diff = {"kind": "git_diff", "state": "capture_failed", "stderr": str(redact_secrets(str(error)))}
        review = _review_from_diff(diff)
        uncommitted = exclude_aido_artifacts(
            [
                str(item.get(key) or "")
                for item in diff.get("status") or []
                if isinstance(item, dict)
                for key in ("path", "previousPath")
                if item.get(key)
            ]
        )
        if review["state"] == "captured" and uncommitted:
            review = {**review, "state": "uncommitted_changes"}
            diff = {
                **diff,
                "stderr": "uncommitted changes remain outside the story commits: "
                + ", ".join(uncommitted[:20]),
            }
        if review["state"] != "captured":
            reason = (
                "Product Loop cumulative diff could not be captured from the thread branch: "
                f"{diff.get('stderr') or review['state']}"
            )
            return coordinator._block_run(
                run.loop,
                stage="review",
                reason=reason,
                actor=run.actor,
                details={
                    "status": str(review.get("state") or "capture_failed"),
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "review": review,
                    "baseRefs": base_refs,
                    "teamSchedule": run.team_schedule,
                    "agentTaskIds": [task["id"] for task in run.agent_tasks],
                },
                thread_id=run.thread_id,
            )
        run.cumulative_patch_artifact_id = _write_cumulative_patch_artifact(coordinator, run, diff)
    if not review["changedFiles"]:
        return block_without_changed_files(coordinator, run, review, runtime_status=run.runtime_status)
    run.review = review
    return None


def _write_cumulative_patch_artifact(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, diff: dict[str, Any]
) -> str:
    from local_control_center.evidence.artifacts import write_text_artifact

    patch = str(diff.get("patchFull") or "")
    root = run.effective_root or coordinator.root
    if not patch or root is None:
        return ""
    artifact_id = f"artifact-{uuid.uuid4()}"
    artifact_file = write_text_artifact(
        root=Path(root), artifact_id=artifact_id, suffix=".patch", content=patch
    )
    artifact = coordinator.evidence.create_artifact(
        artifact_id=artifact_id,
        project_id=run.project_id,
        evidence_package_id=None,
        kind="git_patch",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"] or hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        metadata={
            "name": "product-loop-cumulative.diff",
            "source": "product_loop_coordinator",
            "mimeType": "text/x-diff",
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
            "baseRef": diff.get("baseRef"),
            "loopId": run.loop["id"],
        },
    )
    return str(artifact["id"])
