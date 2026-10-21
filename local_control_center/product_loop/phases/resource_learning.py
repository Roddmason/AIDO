"""Aprendizaje del ResourceManager: observa el resultado de cada recurso asignado y lo persiste.

Fase del pipeline extraída de ``coordinator.py``: cada función recibe el coordinator (dueño de la
conexión y del loop durable) y registra outcome, costo y ruteo de los recursos que ejecutaron el run
—roles del equipo o el Product Owner— para que la selección futura aprenda de la evidencia. Las
variantes ``*_best_effort`` nunca rompen el run: una falla de persistencia queda como estado
``persistence_failed`` en el resumen.

@author Rodrigo Mason
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.decision_engine.observers import record_resource_outcome
from local_control_center.product_loop.fsm import fsm_of
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

__all__ = [
    "attach_product_owner_resource_learning_to_result",
    "attach_resource_learning_to_result",
    "block_after_runtime_with_resource_learning",
    "meter_loop_budget_usage",
    "product_owner_resource_learning_persistence_failed",
    "record_product_owner_resource_learning",
    "record_product_owner_resource_learning_best_effort",
    "record_resource_decision_learning",
    "record_resource_learning",
    "record_resource_learning_best_effort",
    "resource_learning_persistence_failed",
    "resource_observation_targets",
]


def resource_observation_targets(
    coordinator: ProductLoopCoordinator,
    *,
    team_schedule: dict[str, Any],
    runtime_result: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Empareja cada rol con recurso seleccionado con su entrada de uso real (o un rol de ejecución de respaldo)."""
    roles = [
        role_plan
        for role_plan in team_schedule.get("roles") or []
        if isinstance((role_plan.get("resourceDecision") or {}).get("selected"), dict)
        and (role_plan.get("resourceDecision") or {}).get("selected")
    ]
    usage_entries = coordinator._runtime_resource_usage_entries(runtime_result)
    if usage_entries:
        targets: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for entry in usage_entries:
            for role_plan in roles:
                selected = (role_plan.get("resourceDecision") or {}).get("selected") or {}
                if coordinator._resource_role_matches(
                    role_plan, entry
                ) and coordinator._resource_selection_matches(
                    selected,
                    entry,
                ):
                    targets.append((role_plan, entry))
                    break
        return targets
    execution_roles = [
        role_plan
        for role_plan in roles
        if role_plan.get("kind") in {"build", "review"}
        or "code_edit" in (role_plan.get("capabilities") or [])
    ]
    fallback = execution_roles or roles
    return [(fallback[0], None)] if fallback else []


def meter_loop_budget_usage(
    coordinator: ProductLoopCoordinator, loop_id: str | None, cost: dict[str, Any]
) -> None:
    """Acumula en el presupuesto del loop el consumo de una ejecución, solo en las claves con límite.

    Un loop sin presupuesto (o sin claves medibles) no se toca. Las claves medibles son ``agentRuns``
    (1 por ejecución), ``costUsd`` y ``tokens``. Un fallo de medición nunca rompe el run: el siguiente
    checkpoint simplemente evaluará con el consumo ya registrado.
    """
    if not loop_id:
        return
    try:
        budget = fsm_of(coordinator.repository.get_loop(loop_id))["policy"].get("budget") or {}
        measured = {
            "agentRuns": 1,
            "costUsd": cost.get("actualCostUsd"),
            "tokens": cost.get("totalTokens"),
        }
        delta = {key: value for key, value in measured.items() if key in budget and value is not None}
        if delta:
            coordinator.record_usage(loop_id, delta)
    except Exception:
        logger.warning("product_loop.budget_metering_failed loop_id=%s", loop_id, exc_info=True)


def record_resource_decision_learning(
    coordinator: ProductLoopCoordinator,
    *,
    manager: AIResourceManager,
    role: str,
    decision: dict[str, Any],
    runtime_result: dict[str, Any],
    usage_entry: dict[str, Any] | None,
    evidence_ref: str,
    success: bool,
    rework: bool,
    quality_score: float,
    loop_id: str | None = None,
) -> dict[str, Any] | None:
    """Registra outcome, costo y ruteo de una decisión de recurso; ``None`` si la selección está incompleta.

    Con ``loop_id`` además mide el consumo en el presupuesto del loop (un único punto por ejecución).
    """
    selected = decision.get("selected") or {}
    provider_id = str(selected.get("providerId") or "")
    model = str(selected.get("model") or "")
    runtime = str(selected.get("runtime") or "")
    if not provider_id or not model or not runtime:
        return None
    effective_success = coordinator._runtime_bool_metric(
        usage_entry,
        keys=("success", "succeeded", "passed", "status"),
        default=success,
    )
    effective_rework = coordinator._runtime_bool_metric(
        usage_entry,
        keys=("rework", "requiresRework", "requires_rework"),
        default=rework,
    )
    effective_quality_score = coordinator._runtime_quality_score_metric(
        usage_entry,
        default=quality_score,
    )
    latency_ms = coordinator._runtime_latency_ms(runtime_result, usage_entry)
    manager.record_outcome(
        provider_id=provider_id,
        model=model,
        runtime=runtime,
        success=effective_success,
        rework=effective_rework,
        quality_score=effective_quality_score,
        latency_ms=latency_ms,
        evidence_ref=evidence_ref,
    )
    cost = manager.record_cost_observation(
        provider_id=provider_id,
        model=model,
        runtime=runtime,
        estimated_cost_usd=decision.get("estimatedCostUsd"),
        actual_cost_usd=coordinator._runtime_actual_cost_usd(runtime_result, usage_entry),
        provider_usage=coordinator._runtime_provider_usage(runtime_result, usage_entry),
        latency_ms=latency_ms,
        evidence_ref=evidence_ref,
    )
    meter_loop_budget_usage(coordinator, loop_id, cost)
    record_resource_outcome(
        coordinator.connection,
        source_decision_id=decision.get("routingDecisionId"),
        outcome={
            "evidence_ref": evidence_ref,
            "execution_succeeded": effective_success,
            "duration_ms": latency_ms,
            "cost": cost.get("actualCostUsd"),
            "tokens": cost.get("totalTokens"),
        },
    )
    return {
        "role": role,
        "providerId": provider_id,
        "model": model,
        "runtime": runtime,
        "success": effective_success,
        "rework": effective_rework,
        "qualityScore": effective_quality_score,
        "latencyMs": latency_ms,
        "costObservationId": cost["id"],
        "tokenStatus": cost["tokenStatus"],
        "costStatus": cost["costStatus"],
        "evidenceRef": evidence_ref,
    }


def record_product_owner_resource_learning(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    resource_decision: dict[str, Any],
    product_owner_result: dict[str, Any],
    product_owner_status: str,
    evidence_ref: str,
) -> dict[str, Any]:
    """Registra el aprendizaje del recurso que corrió el Product Owner según el estado de su salida."""
    if product_owner_result.get("replayedFromLoopId"):
        return {
            "status": "not_applicable",
            "loopId": loop_id,
            "projectId": project_id,
            "evidenceRef": evidence_ref,
            "observationCount": 0,
            "observations": [],
        }
    valid_completion = product_owner_status in {
        "backlog_ready",
        "brief_ready",
        "completed",
        "scope_is_clear",
    }
    success = valid_completion or product_owner_status == "needs_input"
    quality_score = 1.0 if valid_completion else 0.8 if product_owner_status == "needs_input" else 0.0
    observation = coordinator._record_resource_decision_learning(
        manager=AIResourceManager(coordinator.connection),
        role="product_owner",
        decision=resource_decision,
        runtime_result=product_owner_result,
        usage_entry=None,
        evidence_ref=evidence_ref,
        success=success,
        rework=False,
        quality_score=quality_score,
        loop_id=loop_id,
    )
    observations = [observation] if observation else []
    return {
        "status": "recorded" if observations else "not_applicable",
        "loopId": loop_id,
        "projectId": project_id,
        "evidenceRef": evidence_ref,
        "observationCount": len(observations),
        "observations": observations,
    }


def product_owner_resource_learning_persistence_failed(
    *,
    project_id: str,
    loop_id: str,
    evidence_ref: str,
    error: Exception,
) -> dict[str, Any]:
    """Resumen ``persistence_failed`` del aprendizaje del Product Owner, con el error redactado."""
    return {
        "status": "persistence_failed",
        "loopId": loop_id,
        "projectId": project_id,
        "evidenceRef": evidence_ref,
        "role": "product_owner",
        "reason": str(redact_secrets(str(error))),
        "observationCount": 0,
        "observations": [],
    }


def record_product_owner_resource_learning_best_effort(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    resource_decision: dict[str, Any],
    product_owner_result: dict[str, Any],
    product_owner_status: str,
    evidence_ref: str,
) -> dict[str, Any]:
    """Como ``record_product_owner_resource_learning`` pero degrada cualquier error a ``persistence_failed``."""
    try:
        return coordinator._record_product_owner_resource_learning(
            project_id=project_id,
            loop_id=loop_id,
            resource_decision=resource_decision,
            product_owner_result=product_owner_result,
            product_owner_status=product_owner_status,
            evidence_ref=evidence_ref,
        )
    except Exception as error:
        return product_owner_resource_learning_persistence_failed(
            project_id=project_id,
            loop_id=loop_id,
            evidence_ref=evidence_ref,
            error=error,
        )


def record_resource_learning(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    team_schedule: dict[str, Any],
    runtime_result: dict[str, Any],
    evidence_ref: str,
    success: bool,
    rework: bool,
    quality_score: float,
) -> dict[str, Any]:
    """Registra el aprendizaje de cada recurso del equipo que ejecutó el run."""
    observations: list[dict[str, Any]] = []
    manager = AIResourceManager(coordinator.connection)
    for role_plan, usage_entry in resource_observation_targets(
        coordinator,
        team_schedule=team_schedule,
        runtime_result=runtime_result,
    ):
        decision = role_plan.get("resourceDecision") or {}
        observation = coordinator._record_resource_decision_learning(
            manager=manager,
            role=str(role_plan.get("role") or ""),
            decision=decision,
            runtime_result=runtime_result,
            usage_entry=usage_entry,
            evidence_ref=evidence_ref,
            success=success,
            rework=rework,
            quality_score=quality_score,
            loop_id=loop_id,
        )
        if observation:
            observations.append(observation)
    return {
        "status": "recorded" if observations else "not_applicable",
        "loopId": loop_id,
        "projectId": project_id,
        "evidenceRef": evidence_ref,
        "observationCount": len(observations),
        "observations": observations,
    }


def resource_learning_persistence_failed(
    *,
    project_id: str,
    loop_id: str,
    evidence_ref: str,
    error: Exception,
) -> dict[str, Any]:
    """Resumen ``persistence_failed`` del aprendizaje del equipo, con el error redactado."""
    return {
        "status": "persistence_failed",
        "loopId": loop_id,
        "projectId": project_id,
        "evidenceRef": evidence_ref,
        "reason": str(redact_secrets(str(error))),
        "observationCount": 0,
        "observations": [],
    }


def record_resource_learning_best_effort(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    team_schedule: dict[str, Any],
    runtime_result: dict[str, Any],
    evidence_ref: str,
    success: bool,
    rework: bool,
    quality_score: float,
) -> dict[str, Any]:
    """Como ``record_resource_learning`` pero degrada cualquier error a ``persistence_failed``."""
    try:
        return coordinator._record_resource_learning(
            project_id=project_id,
            loop_id=loop_id,
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=evidence_ref,
            success=success,
            rework=rework,
            quality_score=quality_score,
        )
    except Exception as error:
        return resource_learning_persistence_failed(
            project_id=project_id,
            loop_id=loop_id,
            evidence_ref=evidence_ref,
            error=error,
        )


def attach_resource_learning_to_result(
    coordinator: ProductLoopCoordinator,
    result: dict[str, Any],
    resource_learning: dict[str, Any],
) -> dict[str, Any]:
    """Guarda el resumen de aprendizaje del equipo en el ``durableRun`` del loop del resultado."""
    loop = result["loop"]
    durable = coordinator._durable_run_context(loop)
    durable["resourceLearning"] = resource_learning
    durable["updatedAt"] = utc_now()
    result["loop"] = coordinator._write_context_entries(loop, {"durableRun": redact_secrets(durable)})
    return result


def block_after_runtime_with_resource_learning(
    coordinator: ProductLoopCoordinator,
    loop: dict[str, Any],
    *,
    project_id: str,
    stage: str,
    reason: str,
    actor: str,
    details: dict[str, Any],
    thread_id: str | None,
    team_schedule: dict[str, Any],
    runtime_result: dict[str, Any],
    success: bool = False,
    rework: bool = True,
    quality_score: float = 0.0,
) -> dict[str, Any]:
    """Block after runtime execution and preserve ResourceManager learning evidence."""
    blocked_result = coordinator._block_run(
        loop,
        stage=stage,
        reason=reason,
        actor=actor,
        details=details,
        thread_id=thread_id,
    )
    evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
    if not evidence_ref:
        return blocked_result
    resource_learning = coordinator._record_resource_learning_best_effort(
        project_id=project_id,
        loop_id=loop["id"],
        team_schedule=team_schedule,
        runtime_result=runtime_result,
        evidence_ref=evidence_ref,
        success=success,
        rework=rework,
        quality_score=quality_score,
    )
    return coordinator._attach_resource_learning_to_result(blocked_result, resource_learning)


def attach_product_owner_resource_learning_to_result(
    coordinator: ProductLoopCoordinator,
    result: dict[str, Any],
    resource_learning: dict[str, Any],
) -> dict[str, Any]:
    """Guarda el resumen de aprendizaje del Product Owner en el ``durableRun`` del loop del resultado."""
    loop = result["loop"]
    durable = coordinator._durable_run_context(loop)
    durable["productOwner"] = {
        **dict(durable.get("productOwner") or {}),
        "resourceLearning": resource_learning,
    }
    durable["updatedAt"] = utc_now()
    result["loop"] = coordinator._write_context_entries(loop, {"durableRun": redact_secrets(durable)})
    return result
