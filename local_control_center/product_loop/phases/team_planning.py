"""Fase de planificación de equipo: backlog a agent_tasks, schedule, recursos y asignaciones.

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

__all__ = ["plan_team_and_resources"]


def plan_team_and_resources(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Arma el plan del equipo desde el backlog: agent_tasks, team schedule, recursos IA y assignments.

    Transiciona a planning/backlog_ready y cierra los runs plan-only; devuelve el resultado
    terminal de un bloqueo o del cierre plan-only, o ``None`` para continuar a la ejecución.
    """
    from local_control_center.shared.redaction import redact_secrets

    project_id = run.project_id
    actor = run.actor
    loop = run.loop
    thread_id = run.thread_id
    message_text = run.message_text
    request_meta = run.request_meta
    plan_only = run.plan_only
    effective_root = run.effective_root
    git_state = run.git_state
    assessment_result = run.assessment_result
    technical_lead_runner = run.technical_lead_runner
    output = run.output
    product_owner_result = run.product_owner_result
    product_owner_context = run.product_owner_context
    product_owner_output_record = run.product_owner_output_record
    product_owner_workspace = run.product_owner_workspace
    brief = run.brief
    po_artifact_ids = run.po_artifact_ids
    po_evidence = run.po_evidence
    evidence_ids = run.evidence_ids
    try:
        backlog = coordinator._persist_product_owner_backlog(
            project_id=project_id,
            output=output,
            result=product_owner_result,
            product_owner_output_id=product_owner_output_record["id"],
        )
    except Exception as error:
        reason = f"ProductOwnerAgent backlog persistence failed: {redact_secrets(str(error))}"
        return coordinator._block_run(
            loop,
            stage="backlog",
            reason=reason,
            actor=actor,
            details={
                "status": "persistence_failed",
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "workspaceId": product_owner_workspace["id"],
                "reason": reason,
            },
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    if not backlog:
        return coordinator._block_run(
            loop,
            stage="backlog",
            reason="ProductOwnerAgent returned backlog_ready without epics/user_stories/acceptance_criteria.",
            actor=actor,
            details={"productOwnerOutputId": product_owner_output_record["id"]},
            durable_context={"productOwner": product_owner_context},
            thread_id=thread_id,
        )
    backlog_artifact = coordinator._write_json_artifact(
        root=effective_root,
        project_id=project_id,
        name="backlog.json",
        kind="product_backlog",
        payload={"backlog": backlog, "productOwnerOutputId": product_owner_output_record["id"]},
    )
    coordinator._attach_thread_artifacts(thread_id=thread_id, artifacts=[backlog_artifact])
    coordinator.evidence.attach_artifact_to_evidence(
        artifact_id=backlog_artifact["id"], evidence_package_id=po_evidence["id"]
    )
    product_owner_context["artifactIds"] = [*po_artifact_ids, backlog_artifact["id"]]
    try:
        preliminary_team_schedule = coordinator._team_schedule(
            message=message_text,
            request_meta=request_meta,
            output=output,
            assessment_result=assessment_result,
            git_state=git_state,
        )
    except Exception as error:
        reason = f"TeamScheduler failed to create the preliminary role schedule: {redact_secrets(str(error))}"
        failed_team_schedule = coordinator._failed_team_schedule(phase="preliminary", reason=reason)
        return coordinator._block_run(
            loop,
            stage="team_scheduler",
            reason=reason,
            actor=actor,
            details={
                "productOwnerOutputId": product_owner_output_record["id"],
                "backlogArtifactId": backlog_artifact["id"],
                "teamSchedule": failed_team_schedule,
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": [],
                "teamSchedule": failed_team_schedule,
            },
            thread_id=thread_id,
        )
    raw_plan: dict[str, Any] = {}
    try:
        agent_tasks = coordinator._generate_agent_tasks(
            project_id=project_id,
            loop_id=loop["id"],
            backlog=backlog,
            product_owner_output_id=product_owner_output_record["id"],
            technical_lead_runner=technical_lead_runner,
            team_schedule=preliminary_team_schedule,
            product_owner_output=output,
            assessment_result=assessment_result,
            git_state=git_state,
            plan_sink=raw_plan,
        )
    except Exception as error:
        reason = f"TechnicalLeadPlanner failed to generate agent_tasks: {redact_secrets(str(error))}"
        return coordinator._block_run(
            loop,
            stage="technical_lead",
            reason=reason,
            actor=actor,
            details={
                "productOwnerOutputId": product_owner_output_record["id"],
                "backlogArtifactId": backlog_artifact["id"],
                "teamSchedule": preliminary_team_schedule,
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": [],
                "teamSchedule": preliminary_team_schedule,
            },
            thread_id=thread_id,
        )
    if not agent_tasks:
        return coordinator._block_run(
            loop,
            stage="technical_lead",
            reason="TechnicalLead did not generate agent_tasks; DeveloperAgent execution is not allowed.",
            actor=actor,
            details={
                "productOwnerOutputId": product_owner_output_record["id"],
                "backlogArtifactId": backlog_artifact["id"],
                "teamSchedule": preliminary_team_schedule,
                "agentTaskIds": [],
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": [],
                "teamSchedule": preliminary_team_schedule,
            },
            thread_id=thread_id,
        )
    try:
        team_schedule = coordinator._team_schedule(
            message=message_text,
            request_meta=request_meta,
            output=output,
            assessment_result=assessment_result,
            git_state=git_state,
            agent_tasks=agent_tasks,
        )
    except Exception as error:
        reason = f"TeamScheduler failed to align TechnicalLead agent_tasks: {redact_secrets(str(error))}"
        failed_team_schedule = coordinator._failed_team_schedule(
            phase="final",
            reason=reason,
            previous_schedule=preliminary_team_schedule,
        )
        return coordinator._block_run(
            loop,
            stage="team_scheduler",
            reason=reason,
            actor=actor,
            details={
                "productOwnerOutputId": product_owner_output_record["id"],
                "backlogArtifactId": backlog_artifact["id"],
                "teamSchedule": failed_team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": failed_team_schedule,
            },
            thread_id=thread_id,
        )
    unscheduled_roles = coordinator._unscheduled_agent_task_roles(
        agent_tasks=agent_tasks,
        team_schedule=team_schedule,
    )
    if unscheduled_roles:
        return coordinator._block_run(
            loop,
            stage="technical_lead",
            reason=(
                "TechnicalLead generated agent task roles not covered by TeamScheduler: "
                f"{', '.join(unscheduled_roles)}. DeveloperAgent execution is not allowed until "
                "TeamScheduler covers every task role."
            ),
            actor=actor,
            details={
                "productOwnerOutputId": product_owner_output_record["id"],
                "unscheduledRoles": unscheduled_roles,
                "scheduledRoles": [role["role"] for role in team_schedule.get("roles") or []],
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": team_schedule,
            },
            thread_id=thread_id,
        )
    try:
        team_schedule, resource_blockers = coordinator._team_schedule_with_resource_decisions(
            project_id=project_id,
            loop_id=loop["id"],
            request_meta=request_meta,
            team_schedule=team_schedule,
            agent_tasks=agent_tasks,
        )
    except Exception as error:
        reason = f"AIResourceManager failed to select AI resources: {redact_secrets(str(error))}"
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=reason,
            actor=actor,
            details={
                "resourceBlockers": [{"role": "team_scheduler", "reason": reason}],
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": team_schedule,
            },
            thread_id=thread_id,
        )
    if resource_blockers:
        blocked_role = resource_blockers[0]["role"]
        blocked_reason = resource_blockers[0]["reason"]
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=f"AIResourceManager could not select an approved AI resource for role {blocked_role}: {blocked_reason}",
            actor=actor,
            details={
                "resourceBlockers": resource_blockers,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": team_schedule,
            },
            thread_id=thread_id,
        )
    try:
        team_assignments = coordinator._create_team_assignments(
            project_id=project_id,
            loop_id=loop["id"],
            agent_tasks=agent_tasks,
            team_schedule=team_schedule,
        )
    except Exception as error:
        reason = f"TeamScheduler failed to persist agent assignments: {redact_secrets(str(error))}"
        return coordinator._block_run(
            loop,
            stage="team_scheduler",
            reason=reason,
            actor=actor,
            details={
                "status": "assignment_persistence_failed",
                "productOwnerOutputId": product_owner_output_record["id"],
                "backlogArtifactId": backlog_artifact["id"],
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
                "reason": reason,
            },
            durable_context={
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": team_schedule,
                "agentAssignments": [],
            },
            thread_id=thread_id,
        )

    loop = coordinator._transition_run_state(
        loop,
        to_state="planning",
        reason="TechnicalLead generated agent_tasks from the ProductOwnerAgent backlog.",
        trigger="technical_lead_planning",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {
                "status": "planning",
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": team_schedule,
                "agentAssignments": team_assignments,
            },
            evidence_package_ids=evidence_ids,
        ),
        thread_id=thread_id,
    )
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="agent_tasks_ready",
        agent_role="technical_lead",
        payload={
            "loopId": loop["id"],
            "agentTaskIds": [task["id"] for task in agent_tasks],
            "productOwnerOutputId": product_owner_output_record["id"],
            "teamSchedule": team_schedule["summary"],
            "agentAssignmentIds": [assignment["id"] for assignment in team_assignments],
        },
    )
    loop = coordinator._run_plan_phase(
        run,
        loop=loop,
        raw_plan=raw_plan,
        team_schedule=team_schedule,
        agent_tasks=agent_tasks,
        actor=actor,
        thread_id=thread_id,
    )
    loop = coordinator._transition_run_state(
        loop,
        to_state="backlog_ready",
        reason="ProductOwnerAgent backlog and TechnicalLead agent_tasks are persisted.",
        trigger="product_owner_backlog_ready",
        actor=actor,
        context_patch=coordinator._durable_run_patch(
            loop,
            {
                "status": "backlog_ready",
                "productOwner": product_owner_context,
                "backlog": {"artifactId": backlog_artifact["id"]},
                "agentTasks": agent_tasks,
                "teamSchedule": team_schedule,
                "agentAssignments": team_assignments,
            },
            evidence_package_ids=evidence_ids,
        ),
        thread_id=thread_id,
    )
    if plan_only:
        return coordinator._complete_plan_only(
            loop,
            actor=actor,
            thread_id=thread_id,
            product_owner_output_id=product_owner_output_record["id"],
            backlog_artifact_id=backlog_artifact["id"],
            agent_tasks=agent_tasks,
            team_schedule=team_schedule,
            team_assignments=team_assignments,
            evidence_ids=evidence_ids,
        )
    run.loop = loop
    run.backlog_artifact = backlog_artifact
    run.agent_tasks = agent_tasks
    run.team_schedule = team_schedule
    run.team_assignments = team_assignments
    return None
