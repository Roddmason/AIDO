"""Fases de descubrimiento: corre el ProductOwnerAgent, persiste sus resultados y enruta su salida.

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

__all__ = ["persist_product_owner_results", "route_product_owner_outcome", "run_discovery_phase"]


def run_discovery_phase(coordinator: ProductLoopCoordinator, run: _UserMessageRun) -> dict[str, Any] | None:
    """Transiciona a discovery, asigna el workspace del PO y ejecuta ProductOwnerAgent.

    Devuelve el resultado terminal de un bloqueo de workspace/ejecución o ``None`` para continuar.
    """
    from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
    from local_control_center.product_loop.repository import stable_task_suffix
    from local_control_center.project_constitution.prompt import render_constitution_prompt
    from local_control_center.settings.resolver import resolve_setting_value
    from local_control_center.shared.redaction import redact_secrets
    from local_control_center.workspaces_projects.repository import (
        WorkspaceConflictError,
        WorkspaceIsolationError,
        WorkspacesRepository,
    )

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread = run.thread
    thread_id = run.thread_id
    message_text = run.message_text
    effective_root = run.effective_root
    preferred_runtime = run.preferred_runtime
    assessment_result = run.assessment_result
    product_owner = run.product_owner
    product_owner_task_id = run.product_owner_task_id
    product_owner_resource_decision = run.product_owner_resource_decision
    product_owner_selected_resource = run.product_owner_selected_resource
    product_owner_preferred_runtime = run.product_owner_preferred_runtime
    loop = coordinator._transition_run_state(
        loop,
        to_state="discovery",
        reason="ProductOwnerAgent discovery started after git and assessment gates.",
        trigger="product_owner_discovery",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {
                "status": "discovery",
                "assessment": redact_secrets(assessment_result or {}),
                "productOwner": {"status": "running"},
            },
        ),
        thread_id=thread_id,
    )

    try:
        product_owner_workspace = WorkspacesRepository(
            coordinator.connection, root=effective_root
        ).allocate_workspace(
            project_id=project_id,
            task_id=product_owner_task_id,
            agent_id=PRODUCT_OWNER_AGENT_ID,
            reason="ProductLoopCoordinator ProductOwnerAgent workspace",
            branch_name=(
                f"{coordinator._work_branch_prefix(project_id)}/product-owner-"
                f"{stable_task_suffix(thread_id, loop['id'])}"
            ),
            base_branch=coordinator._resolve_project_base_branch(project_id),
            reuse_existing=True,
        )
    except (WorkspaceConflictError, WorkspaceIsolationError, ValueError, KeyError) as error:
        return coordinator._block_run(
            loop,
            stage="product_owner_workspace",
            reason=str(error),
            actor=actor,
            details={"taskId": product_owner_task_id},
            thread_id=thread_id,
        )

    goal_statement = str(
        resolve_setting_value(
            connection=coordinator.connection,
            key="project.goal.statement",
            project_id=project_id,
        )
        or ""
    ).strip()
    product_owner_payload = {
        "projectId": project_id,
        "workspaceId": product_owner_workspace["id"],
        "taskId": product_owner_task_id,
        "idea": message_text,
        "preferredRuntime": product_owner_preferred_runtime or preferred_runtime,
        "model": product_owner_selected_resource.get("model"),
        # Solo lo que el agente convierte en señales de prompt: el resultado completo del
        # assessment (job/agentRun/artifact/decision) ya queda auditado en su propio slice.
        "assessment": redact_secrets(
            {
                key: (assessment_result or {}).get(key)
                for key in ("status", "assessment", "findings")
                if (assessment_result or {}).get(key) is not None
            }
        ),
        "workflowContext": {
            "workflowRunId": loop["id"],
            "workflowStepId": "product_owner",
        },
        "metadata": {
            "loopId": loop["id"],
            "projectThreadId": thread.get("id"),
            "source": "product_loop_coordinator",
            "resourceSelection": product_owner_resource_decision,
        },
    }
    if goal_statement:
        product_owner_payload["goalStatement"] = goal_statement
    constitution_prompt = render_constitution_prompt(run.constitution)
    if constitution_prompt:
        product_owner_payload["constitution"] = constitution_prompt
    # Sin la iniciativa del hilo el agente no ve el brief ni las preguntas ya formuladas, y
    # vuelve a preguntar lo mismo en cada turno.
    thread_initiative = (
        coordinator.discovery.find_initiative_by_thread(project_id, thread_id) if thread_id else None
    )
    if thread_initiative is not None:
        product_owner_payload["initiativeId"] = thread_initiative["id"]
    try:
        product_owner_result = product_owner.run(product_owner_payload)
    except Exception as error:
        reason = str(redact_secrets(str(error)))
        product_owner_context = {
            "status": "runtime_failed",
            "reason": reason,
            "workspaceId": product_owner_workspace["id"],
            "resourceDecision": product_owner_resource_decision,
        }
        blocked_result = coordinator._block_run(
            loop,
            stage="product_owner",
            reason=reason,
            actor=actor,
            details={
                "status": "runtime_failed",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
        evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
        if evidence_ref:
            resource_learning = coordinator._record_product_owner_resource_learning_best_effort(
                project_id=project_id,
                loop_id=loop["id"],
                resource_decision=product_owner_resource_decision,
                product_owner_result={"status": "runtime_failed", "reason": reason},
                product_owner_status="runtime_failed",
                evidence_ref=evidence_ref,
            )
            blocked_result = coordinator._attach_product_owner_resource_learning_to_result(
                blocked_result,
                resource_learning,
            )
        return blocked_result
    run.loop = loop
    run.product_owner_workspace = product_owner_workspace
    run.product_owner_result = product_owner_result
    return None


def persist_product_owner_results(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Valida y persiste la salida del PO: artefactos, brief, preguntas, decisiones y evidencia.

    Construye ``product_owner_context`` y los ids de evidencia del PO; devuelve el resultado
    terminal de un bloqueo de validación/persistencia o ``None`` para continuar.
    """
    from local_control_center.agents.product_owner_agent import ProductOwnerOutputValidationError
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread = run.thread
    thread_id = run.thread_id
    message_text = run.message_text
    resolved_title = run.resolved_title
    effective_root = run.effective_root
    product_owner_result = run.product_owner_result
    product_owner_workspace = run.product_owner_workspace
    product_owner_resource_decision = run.product_owner_resource_decision
    try:
        output = coordinator._product_owner_output(product_owner_result)
        product_owner_status = coordinator._product_owner_flow_status(product_owner_result, output=output)
    except ProductOwnerOutputValidationError as error:
        raw_output = product_owner_result.get("output") if isinstance(product_owner_result, dict) else None
        details = {
            "status": "failed_validation",
            "outputStatus": product_owner_result.get("status")
            if isinstance(product_owner_result, dict)
            else None,
            "reason": product_owner_result.get("reason") if isinstance(product_owner_result, dict) else None,
            "outputType": type(raw_output).__name__,
        }
        product_owner_context = {
            "status": "failed_validation",
            "reason": str(redact_secrets(str(error))),
            "workspaceId": product_owner_workspace["id"],
            "resourceDecision": product_owner_resource_decision,
        }
        blocked_result = coordinator._block_run(
            loop,
            stage="product_owner",
            reason=str(redact_secrets(str(error))),
            actor=actor,
            details=details,
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
        evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
        if evidence_ref and isinstance(product_owner_result, dict):
            resource_learning = coordinator._record_product_owner_resource_learning_best_effort(
                project_id=project_id,
                loop_id=loop["id"],
                resource_decision=product_owner_resource_decision,
                product_owner_result=product_owner_result,
                product_owner_status="failed_validation",
                evidence_ref=evidence_ref,
            )
            blocked_result = coordinator._attach_product_owner_resource_learning_to_result(
                blocked_result,
                resource_learning,
            )
        return blocked_result
    try:
        product_owner_artifact = coordinator._write_json_artifact(
            root=effective_root,
            project_id=project_id,
            name="product_owner_output.json",
            kind="product_owner_output",
            payload={"status": product_owner_status, "result": product_owner_result, "output": output},
        )
    except Exception as error:
        reason = f"ProductOwnerAgent output artifact persistence failed: {redact_secrets(str(error))}"
        product_owner_context = {
            "status": "persistence_failed",
            "reason": reason,
            "workspaceId": product_owner_workspace["id"],
            "resourceDecision": product_owner_resource_decision,
        }
        return coordinator._block_run(
            loop,
            stage="product_owner",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "workspaceId": product_owner_workspace["id"],
                "reason": reason,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    try:
        initiative = coordinator._ensure_product_initiative(
            project_id=project_id,
            message=message_text,
            title=resolved_title,
            result=product_owner_result,
            output=output,
            thread_id=thread_id,
        )
        brief = coordinator._persist_product_brief(
            project_id=project_id,
            initiative_id=initiative["id"],
            title=resolved_title,
            result=product_owner_result,
            output=output,
        )
        brief_artifact = coordinator._write_json_artifact(
            root=effective_root,
            project_id=project_id,
            name="product_brief.json",
            kind="product_brief",
            payload={"brief": brief, "productBriefPatch": output.get("productBriefPatch") or {}},
        )
    except Exception as error:
        reason = f"ProductOwnerAgent brief persistence failed: {redact_secrets(str(error))}"
        product_owner_context = {
            "status": "persistence_failed",
            "reason": reason,
            "workspaceId": product_owner_workspace["id"],
            "artifactIds": [product_owner_artifact["id"]],
            "resourceDecision": product_owner_resource_decision,
        }
        return coordinator._block_run(
            loop,
            stage="product_owner",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "workspaceId": product_owner_workspace["id"],
                "artifactIds": [product_owner_artifact["id"]],
                "reason": reason,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    try:
        product_owner_output_record = coordinator._persist_product_owner_output_record(
            project_id=project_id,
            initiative_id=initiative["id"],
            brief_id=brief["id"],
            output=output,
            result=product_owner_result,
            artifact_id=product_owner_artifact["id"],
        )
    except Exception as error:
        reason = f"ProductOwnerAgent output persistence failed: {redact_secrets(str(error))}"
        product_owner_context = {
            "status": "persistence_failed",
            "reason": reason,
            "workspaceId": product_owner_workspace["id"],
            "briefId": brief["id"],
            "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
            "resourceDecision": product_owner_resource_decision,
        }
        return coordinator._block_run(
            loop,
            stage="product_owner",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "workspaceId": product_owner_workspace["id"],
                "briefId": brief["id"],
                "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                "reason": reason,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    try:
        clarification_questions = coordinator._persist_clarification_questions(
            project_id=project_id,
            initiative_id=initiative["id"],
            output=output,
            thread_id=thread_id,
            source_message_id=thread["messageId"],
            result=product_owner_result,
        )
        product_decisions = coordinator._persist_product_decisions(
            project_id=project_id,
            initiative_id=initiative["id"],
            brief_id=brief["id"],
            output=output,
            thread_id=thread_id,
            source_message_id=thread["messageId"],
            result=product_owner_result,
        )
        pending_thread_decisions = coordinator._pending_product_owner_thread_decisions(thread_id)
    except Exception as error:
        reason = f"ProductOwnerAgent question/decision persistence failed: {redact_secrets(str(error))}"
        product_owner_context = {
            "status": "persistence_failed",
            "reason": reason,
            "workspaceId": product_owner_workspace["id"],
            "productOwnerOutputId": product_owner_output_record["id"],
            "briefId": brief["id"],
            "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
            "resourceDecision": product_owner_resource_decision,
        }
        return coordinator._block_run(
            loop,
            stage="product_owner",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "workspaceId": product_owner_workspace["id"],
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                "reason": reason,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    if product_owner_status == "needs_input" and not pending_thread_decisions:
        reason = (
            "ProductOwnerAgent returned needs_input but did not create a pending actionable "
            "thread decision with options."
        )
        product_owner_context = {
            "status": "failed_validation",
            "reason": reason,
            "workspaceId": product_owner_workspace["id"],
            "productOwnerOutputId": product_owner_output_record["id"],
            "briefId": brief["id"],
            "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
            "resourceDecision": product_owner_resource_decision,
            "clarificationQuestionIds": [item["id"] for item in clarification_questions],
            "productDecisionIds": [item["id"] for item in product_decisions],
            "pendingThreadDecisions": [],
        }
        blocked_result = coordinator._block_run(
            loop,
            stage="product_owner",
            reason=reason,
            actor=actor,
            details={
                "status": "failed_validation",
                "outputStatus": product_owner_status,
                "workspaceId": product_owner_workspace["id"],
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                "clarificationQuestionIds": [item["id"] for item in clarification_questions],
                "productDecisionIds": [item["id"] for item in product_decisions],
                "pendingThreadDecisions": [],
                "reason": reason,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
        evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
        if evidence_ref:
            resource_learning = coordinator._record_product_owner_resource_learning_best_effort(
                project_id=project_id,
                loop_id=loop["id"],
                resource_decision=product_owner_resource_decision,
                product_owner_result=product_owner_result,
                product_owner_status="failed_validation",
                evidence_ref=evidence_ref,
            )
            blocked_result = coordinator._attach_product_owner_resource_learning_to_result(
                blocked_result,
                resource_learning,
            )
        return blocked_result
    po_artifacts = [product_owner_artifact, brief_artifact]
    coordinator._attach_thread_artifacts(thread_id=thread_id, artifacts=po_artifacts)
    po_artifact_ids = [artifact["id"] for artifact in po_artifacts]
    po_evidence = coordinator._record_run_evidence(
        project_id=project_id,
        loop_id=loop["id"],
        stage="product_owner",
        status=product_owner_status,
        reason=str(product_owner_result.get("reason") or output.get("recommendedNextAction") or ""),
        details={
            "status": product_owner_status,
            "productOwnerOutputId": product_owner_output_record["id"],
            "clarificationQuestionIds": [item["id"] for item in clarification_questions],
            "productDecisionIds": [item["id"] for item in product_decisions],
        },
        workspace_id=product_owner_workspace["id"],
        artifact_ids=po_artifact_ids,
    )
    for artifact_id in po_artifact_ids:
        coordinator.evidence.attach_artifact_to_evidence(
            artifact_id=artifact_id, evidence_package_id=po_evidence["id"]
        )
    try:
        product_owner_resource_learning = coordinator._record_product_owner_resource_learning(
            project_id=project_id,
            loop_id=loop["id"],
            resource_decision=product_owner_resource_decision,
            product_owner_result=product_owner_result,
            product_owner_status=product_owner_status,
            evidence_ref=po_evidence["id"],
        )
    except Exception as error:
        reason = f"ProductOwnerAgent resource learning persistence failed: {redact_secrets(str(error))}"
        resource_learning = {
            "status": "persistence_failed",
            "reason": reason,
            "evidenceRef": po_evidence["id"],
            "role": "product_owner",
        }
        product_owner_context = {
            "status": product_owner_status,
            "reason": product_owner_result.get("reason") or output.get("recommendedNextAction") or "",
            "workspaceId": product_owner_workspace["id"],
            "productOwnerOutputId": product_owner_output_record["id"],
            "briefId": brief["id"],
            "artifactIds": po_artifact_ids,
            "evidencePackageId": po_evidence["id"],
            "resourceDecision": product_owner_resource_decision,
            "resourceLearning": resource_learning,
            "clarificationQuestionIds": [item["id"] for item in clarification_questions],
            "productDecisionIds": [item["id"] for item in product_decisions],
            "pendingThreadDecisions": pending_thread_decisions,
        }
        return coordinator._block_run(
            loop,
            stage="resource_learning",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "reason": reason,
                "role": "product_owner",
                "workspaceId": product_owner_workspace["id"],
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "artifactIds": po_artifact_ids,
                "evidenceRef": po_evidence["id"],
                "resourceDecision": product_owner_resource_decision,
            },
            durable_context={
                "productOwner": product_owner_context,
                "resourceLearning": resource_learning,
            },
            thread_id=thread_id,
        )
    product_owner_context = {
        "status": product_owner_status,
        "reason": product_owner_result.get("reason") or output.get("recommendedNextAction") or "",
        "workspaceId": product_owner_workspace["id"],
        "productOwnerOutputId": product_owner_output_record["id"],
        "briefId": brief["id"],
        "artifactIds": po_artifact_ids,
        "evidencePackageId": po_evidence["id"],
        "resourceDecision": product_owner_resource_decision,
        "resourceLearning": product_owner_resource_learning,
        "clarificationQuestionIds": [item["id"] for item in clarification_questions],
        "productDecisionIds": [item["id"] for item in product_decisions],
        "pendingThreadDecisions": pending_thread_decisions,
    }
    evidence_ids = [*coordinator._external_evidence_ids(product_owner_result), po_evidence["id"]]
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="product_owner_completed",
        agent_role="product_owner",
        payload={"loopId": loop["id"], **product_owner_context},
    )
    run.output = output
    run.product_owner_status = product_owner_status
    run.brief = brief
    run.product_owner_output_record = product_owner_output_record
    run.pending_thread_decisions = pending_thread_decisions
    run.po_artifact_ids = po_artifact_ids
    run.po_evidence = po_evidence
    run.product_owner_context = product_owner_context
    run.evidence_ids = evidence_ids
    return None


def route_product_owner_outcome(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Enruta el estado del PO: needs_input, blocked, research requerida o brief_ready.

    Cada rama devuelve su resultado terminal; ``None`` significa backlog_ready y el
    pipeline sigue hacia la planificación del equipo.
    """
    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread = run.thread
    thread_id = run.thread_id
    message_text = run.message_text
    request_meta = run.request_meta
    output = run.output
    product_owner_result = run.product_owner_result
    product_owner_status = run.product_owner_status
    product_owner_workspace = run.product_owner_workspace
    product_owner_context = run.product_owner_context
    pending_thread_decisions = run.pending_thread_decisions
    po_artifact_ids = run.po_artifact_ids
    po_evidence = run.po_evidence
    evidence_ids = run.evidence_ids
    brief = run.brief
    if product_owner_status == "needs_input":
        coordinator._set_thread_status_best_effort(
            thread_id=thread_id,
            status="waiting_decision",
            reason="ProductOwnerAgent requires product clarification before development.",
        )
        awaiting = coordinator._transition_run_state(
            loop,
            to_state="awaiting_user",
            reason=product_owner_context["reason"]
            or "ProductOwnerAgent requires product clarification before development.",
            trigger="product_owner_needs_input",
            actor=actor,
            context_patch=coordinator._durable_run_patch(
                loop,
                {
                    "status": "awaiting_user",
                    "productOwner": product_owner_context,
                },
                evidence_package_ids=evidence_ids,
            ),
            thread_id=thread_id,
        )
        if thread_id and pending_thread_decisions:
            coordinator._create_blocker_remediations_best_effort(
                project_id=awaiting["projectId"],
                thread_id=thread_id,
                loop_id=awaiting["id"],
                stage="product_owner",
                reason=product_owner_context["reason"]
                or "ProductOwnerAgent requires product question input before development.",
                details={
                    **product_owner_context,
                    "status": "needs_input",
                    "pendingDecisions": pending_thread_decisions,
                },
            )
        return coordinator._run_result(
            awaiting,
            status="awaiting_user",
            reason=product_owner_context["reason"]
            or "ProductOwnerAgent requires product clarification before development.",
            evidence_package=po_evidence,
        )

    if product_owner_status == "blocked":
        return coordinator._block_run(
            loop,
            stage="product_owner",
            reason=str(
                product_owner_result.get("reason") or "ProductOwnerAgent did not produce a usable output."
            ),
            actor=actor,
            details=product_owner_result,
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )

    research_required_decisions = coordinator._high_impact_technical_decisions_requiring_research(
        request_meta=request_meta,
        output=output,
    )
    if research_required_decisions:
        research_job = coordinator._queue_required_research(
            project_id=project_id,
            thread_id=thread_id,
            message_id=thread["messageId"],
            workspace_id=product_owner_workspace["id"],
            loop_id=loop["id"],
            message=message_text,
            request_meta=request_meta,
            decisions=research_required_decisions,
        )
        return coordinator._block_run(
            loop,
            stage="research",
            reason=("ResearchAgent evidence is required before accepting high-impact technical decisions."),
            actor=actor,
            details={
                "jobId": research_job["id"],
                "researchStatus": "research_required",
                "decisions": research_required_decisions,
                "researchPolicy": coordinator._research_policy(request_meta),
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )

    if product_owner_status == "brief_ready":
        approval = None
        if coordinator._requires_brief_approval(
            request_meta=request_meta, result=product_owner_result, output=output
        ):
            approval = coordinator._create_brief_approval(
                project_id=project_id,
                loop_id=loop["id"],
                brief=brief,
                artifact_ids=po_artifact_ids,
            )
            product_owner_context["briefApproval"] = approval
        brief_ready = coordinator._transition_run_state(
            loop,
            to_state="brief_ready",
            reason=product_owner_context["reason"] or "ProductOwnerAgent produced a product brief.",
            trigger="product_owner_brief_ready",
            actor=actor,
            context_patch=coordinator._durable_run_patch(
                loop,
                {
                    "status": "brief_ready",
                    "productOwner": product_owner_context,
                    "briefApproval": approval,
                },
                evidence_package_ids=evidence_ids,
            ),
            thread_id=thread_id,
        )
        return coordinator._run_result(
            brief_ready,
            status="brief_ready",
            reason=product_owner_context["reason"] or "ProductOwnerAgent produced a product brief.",
            evidence_package=po_evidence,
        )
    return None
