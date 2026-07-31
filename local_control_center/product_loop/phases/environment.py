"""Fases de entorno: workspace/git limpios, recursos IA del ProductOwner y assessment del proyecto.

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

__all__ = ["check_workspace_and_git", "run_project_assessment", "select_product_owner_resources"]


def check_workspace_and_git(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Registra agentes de delivery y valida workspace root, limpieza y remotos de git.

    Devuelve el resultado terminal de un bloqueo de workspace/git o ``None`` para continuar.
    """
    from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
    from local_control_center.git_workspace.service import GitWorkspaceService
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread = run.thread
    thread_id = run.thread_id
    request_meta = run.request_meta
    plan_only = run.plan_only
    effective_root = run.effective_root
    git_service = run.git_service
    product_owner_runner = run.product_owner_runner
    assignments = coordinator._ensure_delivery_agents(project_id)
    loop = coordinator._transition_run_state(
        loop,
        to_state="workspace_check",
        reason="User message thread and delivery agents are registered.",
        trigger="workspace_check",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {
                "status": "workspace_check",
                "thread": thread,
                "requestMeta": request_meta,
                "planOnly": plan_only,
                "agentAssignments": assignments,
            },
        ),
        thread_id=thread_id,
    )
    if effective_root is None:
        return coordinator._block_run(
            loop,
            stage="workspace_check",
            reason="ProductLoopCoordinator root is required to create an isolated workspace.",
            actor=actor,
            thread_id=thread_id,
        )

    git = git_service or GitWorkspaceService(coordinator.connection, root=effective_root)
    product_owner = product_owner_runner or ProductOwnerAgentRunner(
        coordinator.connection, root=effective_root
    )

    loop = coordinator._transition_run_state(
        loop,
        to_state="git_check",
        reason="Checking git workspace cleanliness before worktree allocation.",
        trigger="git_check",
        actor=actor,
        context_patch=coordinator._durable_run_patch(loop, {"status": "git_check"}),
        thread_id=thread_id,
    )
    try:
        git_state = git.status(project_id)
    except Exception as error:
        reason = f"Git workspace status check failed: {redact_secrets(str(error))}"
        git_state = {
            "status": "failed",
            "reason": reason,
            "projectId": project_id,
            "dirty": False,
            "changedFiles": [],
            "untrackedFiles": [],
            "stagedFiles": [],
        }
    if git_state.get("status") != "completed":
        reason = str(git_state.get("reason") or "Git status did not complete.")
        return coordinator._block_run(
            loop, stage="git", reason=reason, actor=actor, details=git_state, thread_id=thread_id
        )
    if bool(git_state.get("dirty")):
        reason = "Project git tree is dirty; Product Loop execution requires a clean base."
        return coordinator._block_run(
            loop, stage="git", reason=reason, actor=actor, details=git_state, thread_id=thread_id
        )
    configured_remotes = coordinator.connection.execute(
        "SELECT COUNT(*) AS total FROM git_remotes WHERE project_id = ?",
        (project_id,),
    ).fetchone()["total"]
    if configured_remotes and not (git_state.get("remotes") or []):
        reason = (
            "The project has a configured Git remote, but the repository exposes none; "
            "Product Loop delivery to that remote cannot continue."
        )
        return coordinator._block_run(
            loop,
            stage="git",
            reason=reason,
            actor=actor,
            details={**git_state, "remoteMissing": True, "configuredRemotes": configured_remotes},
            thread_id=thread_id,
        )
    run.loop = loop
    run.git = git
    run.git_state = git_state
    run.product_owner = product_owner
    return None


def select_product_owner_resources(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Selecciona el recurso IA del ProductOwnerAgent y verifica que su runtime sea ejecutable.

    Devuelve el resultado terminal de un bloqueo de recurso/runtime o ``None`` para continuar.
    """
    from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
    from local_control_center.product_loop.repository import stable_task_suffix
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread_id = run.thread_id
    request_meta = run.request_meta
    preferred_runtime = run.preferred_runtime
    product_owner = run.product_owner
    product_owner_task_id = f"product-owner-{stable_task_suffix(thread_id, loop['id'])}"
    try:
        product_owner_resource_decision, product_owner_resource_blocker = (
            coordinator._product_owner_resource_selection(
                project_id=project_id,
                loop_id=loop["id"],
                task_id=product_owner_task_id,
                request_meta=request_meta,
            )
        )
    except Exception as error:
        reason = (
            f"AIResourceManager failed to select a ProductOwnerAgent resource: {redact_secrets(str(error))}"
        )
        product_owner_resource_decision = {
            "selected": None,
            "approvalRequired": False,
            "decisionReason": reason,
            "policyResult": {"status": "failed"},
        }
        resource_blocker = {
            "role": "product_owner",
            "taskId": product_owner_task_id,
            "reason": reason,
            "decision": product_owner_resource_decision,
        }
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=reason,
            actor=actor,
            details={
                "resourceBlockers": [resource_blocker],
                "agentRole": "product_owner",
                "agentId": PRODUCT_OWNER_AGENT_ID,
            },
            durable_context={
                "productOwner": {
                    "status": "resource_blocked",
                    "reason": reason,
                    "resourceDecision": product_owner_resource_decision,
                }
            },
            thread_id=thread_id,
        )
    if product_owner_resource_blocker:
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=(
                "AIResourceManager could not select an approved AI resource for ProductOwnerAgent: "
                f"{product_owner_resource_blocker['reason']}"
            ),
            actor=actor,
            details={
                "resourceBlockers": [product_owner_resource_blocker],
                "agentRole": "product_owner",
                "agentId": PRODUCT_OWNER_AGENT_ID,
            },
            durable_context={
                "productOwner": {
                    "status": "resource_blocked",
                    "resourceDecision": product_owner_resource_decision,
                }
            },
            thread_id=thread_id,
        )
    product_owner_selected_resource = product_owner_resource_decision.get("selected") or {}
    product_owner_preferred_runtime = coordinator._product_owner_runtime_id_for_resource_selection(
        product_owner_selected_resource
    )
    product_owner_effective_runtime = product_owner_preferred_runtime or preferred_runtime

    loop = coordinator._transition_run_state(
        loop,
        to_state="runtime_check",
        reason="Checking executable ProductOwnerAgent runtime after git_check.",
        trigger="product_owner_runtime_check",
        actor=actor,
        context_patch=coordinator._durable_run_patch(loop, {"status": "runtime_check"}),
        thread_id=thread_id,
    )
    if hasattr(product_owner, "status"):
        try:
            product_owner_readiness = product_owner.status(preferred_runtime=product_owner_effective_runtime)
        except Exception as error:
            reason = f"ProductOwnerAgent runtime readiness check failed: {redact_secrets(str(error))}"
            product_owner_readiness = {
                "executable": False,
                "status": "failed",
                "selectedRuntimeId": product_owner_effective_runtime,
                "reason": reason,
            }
    else:
        product_owner_readiness = {
            "executable": True,
            "selectedRuntimeId": product_owner_effective_runtime or "injected_product_owner_runner",
            "reason": "Injected ProductOwnerAgent runner has no readiness hook.",
        }
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="runtime_selected",
        agent_role="product_owner",
        payload={
            "loopId": loop["id"],
            "runtimeId": product_owner_readiness.get("selectedRuntimeId") or product_owner_effective_runtime,
            "executable": bool(product_owner_readiness.get("executable")),
            "reason": product_owner_readiness.get("reason"),
            "resourceSelection": product_owner_resource_decision,
        },
    )
    if not bool(product_owner_readiness.get("executable")):
        reason = str(
            product_owner_readiness.get("reason") or "No executable ProductOwnerAgent runtime is configured."
        )
        product_owner_context = {
            "status": "runtime_unavailable",
            "reason": reason,
            "resourceDecision": product_owner_resource_decision,
            "runtimeReadiness": product_owner_readiness,
        }
        return coordinator._block_run(
            loop,
            stage="product_owner_runtime",
            reason=reason,
            actor=actor,
            details={
                **product_owner_readiness,
                "resourceDecision": product_owner_resource_decision,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    run.loop = loop
    run.product_owner_task_id = product_owner_task_id
    run.product_owner_resource_decision = product_owner_resource_decision
    run.product_owner_selected_resource = product_owner_selected_resource
    run.product_owner_preferred_runtime = product_owner_preferred_runtime
    return None


def run_project_assessment(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Corre el assessment del proyecto existente antes del discovery.

    Devuelve el resultado terminal si el assessment falla o no completa, o ``None`` para continuar.
    """
    from local_control_center.agents.assessment_runner import ProjectAssessmentRunner
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread_id = run.thread_id
    effective_root = run.effective_root
    assessment_runner = run.assessment_runner
    assessment_result: dict[str, Any] | None = None
    if coordinator._project_is_existing(project_id):
        assessment = assessment_runner or ProjectAssessmentRunner(coordinator.connection, root=effective_root)
        try:
            assessment_result = assessment.run(project_id)
        except Exception as error:
            return coordinator._block_run(
                loop,
                stage="project_assessment",
                reason=str(redact_secrets(str(error))),
                actor=actor,
                details={"projectId": project_id},
                thread_id=thread_id,
            )
        if assessment_result.get("status") != "completed":
            reason = str(assessment_result.get("reason") or "Project assessment did not complete.")
            return coordinator._block_run(
                loop,
                stage="project_assessment",
                reason=reason,
                actor=actor,
                details=assessment_result,
                thread_id=thread_id,
            )
    run.assessment_result = assessment_result
    return None
