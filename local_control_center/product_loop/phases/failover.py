"""Failover de runtime: reintenta un rol con el siguiente proveedor elegible cuando el actual falla.

Fase del pipeline extraída de ``coordinator.py``: cada función recibe el coordinator y resuelve el
reemplazo del recurso caído —dentro del orden, la política de transporte y el tope de costo del
rol, y cortando proveedores suspendidos por cuota— o ejecuta el runner con esa política de failover. Las funciones aceptan cualquier
objeto con la interfaz del coordinator (los tests ejercitan el failover con dobles mínimos).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.runtime_failover import (
    MAX_FAILOVER_ATTEMPTS,
    FailureClass,
    classify_runtime_failure,
    exclusion_for,
    is_affordable_candidate,
    should_failover,
)
from local_control_center.product_loop.run_context import UserMessageRun as _UserMessageRun
from local_control_center.runtime_team.configuration import role_allowlist, role_routing_preferences
from local_control_center.runtime_team.roles import team_role_for
from local_control_center.shared.redaction import redact_secrets

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

__all__ = ["failover_replacement", "raise_if_usage_suspended", "run_with_failover"]


def failover_replacement(
    coordinator: ProductLoopCoordinator,
    *,
    run: _UserMessageRun,
    payload: dict[str, Any],
    attempts: list[dict[str, Any]],
    provider_id: str,
    failed_model: str,
    role: str,
) -> dict[str, Any] | None:
    """Elige otro proveedor para reintentar, o ``None`` si ninguno es viable ni asequible.

    El ``task_id`` lleva sufijo por intento porque cada selección inserta una fila inmutable de
    routing: sin el sufijo, el snapshot de costo del hilo sumaría runtimes que nunca corrieron.

    No relaja jamás la política de transporte del rol ni el tope de costo: el failover solo se
    mueve dentro de lo que la política de ``role`` ya permitía.
    """
    excluded = [
        exclusion_for(
            FailureClass(str(item["failureClass"])),
            provider_id=str(item["providerId"]),
            model=str(item["model"]),
        )
        for item in attempts
    ]
    policy = coordinator._resource_role_policy(role)
    manager = AIResourceManager(coordinator.connection)
    try:
        if role == "product_owner":
            decision, _ = coordinator._product_owner_resource_selection(
                project_id=run.project_id,
                loop_id=run.loop["id"],
                task_id=str(payload["taskId"]),
                request_meta=run.request_meta,
                excluded_resources=excluded,
            )
        else:
            order, order_wildcards = role_routing_preferences(
                getattr(run, "request_meta", None), team_role_for(role)
            )

            def failover_request(allowed: list[str] | None) -> AIResourceRequest:
                return AIResourceRequest(
                    project_id=run.project_id,
                    workflow_run_id=run.loop["id"],
                    agent_id=role,
                    agent_profile_id=(coordinator._profile_by_role(run.project_id).get(role) or {}).get("id"),
                    task_id=f"{run.task_id}:f{len(attempts)}",
                    task_type=f"{role}.implement",
                    risk_level=str((run.team_schedule or {}).get("risk") or "medium"),
                    routing_policy=str((run.team_schedule or {}).get("mode") or "balanced"),
                    required_capabilities=["chat"],
                    allowed_provider_ids=allowed
                    if allowed is not None
                    else role_allowlist(getattr(run, "request_meta", None), team_role_for(role)),
                    preferred_provider_ids=[
                        *order,
                        *(item for item in policy["preferredProviderIds"] if item not in order),
                    ],
                    preferred_resources=[*order_wildcards, *policy["preferredResources"]],
                    provider_order=order,
                    blocked_resources=policy["blockedResources"],
                    excluded_resources=excluded,
                    context_token_limit=policy["maxTokensPerRun"],
                    role_policy_id=policy["rolePolicyId"],
                    allow_remote=policy["allowRemote"],
                    allow_local=policy["allowLocal"],
                    allow_cli=policy["allowCli"],
                    allow_api=policy["allowApi"],
                    free_tier_only=policy["freeTierOnly"],
                    allow_unknown_cost=policy["allowUnknownCost"],
                    require_approval_for_unknown_cost=policy["requireApprovalForUnknownCost"],
                    require_approval_over_usd=policy["requiresApprovalOverUsd"],
                )

            decision = coordinator._select_walking_providers(
                manager,
                failover_request,
                None,
                coordinator._jev_provider_walk(
                    run.project_id, getattr(run, "request_meta", None) or {}, team_role_for(role)
                ),
                record=True,
                allow_decision_inference=True,
            )
    except Exception:
        return None
    selected = decision.get("selected") if isinstance(decision, dict) else None
    if not selected:
        return None
    if decision.get("approvalRequired"):
        return None
    affordable, cost_reason = is_affordable_candidate(
        decision,
        requires_approval_over_usd=policy["requiresApprovalOverUsd"],
        allow_unknown_cost=policy["allowUnknownCost"] and not policy["requireApprovalForUnknownCost"],
    )
    if not affordable:
        attempts[-1]["costDecision"] = f"runtime_failover_cost_capped: {cost_reason}"
        return None
    next_runtime_id = str(selected.get("providerId") or "").strip()
    next_model = str(selected.get("model") or "").strip()
    if not next_runtime_id or (next_runtime_id, next_model) == (provider_id, failed_model):
        return None
    coordinator._record_local_model_switches(
        project_id=run.project_id,
        loop_id=run.loop["id"],
        thread_id=run.thread_id,
        role=role,
        decision=decision,
    )
    next_payload = dict(payload)
    next_payload["preferredRuntime"] = next_runtime_id
    if selected.get("model"):
        next_payload["model"] = selected["model"]
    else:
        next_payload.pop("model", None)
    next_payload["resourceSelection"] = coordinator._public_resource_decision(decision)
    if role == "product_owner":
        next_payload["metadata"] = {
            **dict(payload.get("metadata") or {}),
            "resourceSelection": next_payload["resourceSelection"],
        }
    return next_payload


def run_with_failover(
    coordinator: ProductLoopCoordinator,
    *,
    runtime: Any,
    payload: dict[str, Any],
    run: _UserMessageRun,
    attempts: list[dict[str, Any]],
    thread_id: str | None,
    role: str = "developer",
) -> dict[str, Any]:
    """Ejecuta el runtime y recupera rechazos de proveedor con otro candidato autorizado.

    ``role`` conserva la política y el contrato del ejecutor. Product Owner también
    recupera respuestas fallidas estructuradas; nunca se repite salida parcial del developer.

    Solo reintenta lo que dice algo del proveedor y no del trabajo: un fallo de contrato se
    propaga tal cual, porque repetirlo en otro modelo gasta dinero para obtener el mismo error.
    El reemplazo debe además caber en el tope de costo del rol; si no cabe, se propaga la falla
    y el operador decide.

    Cada intento queda registrado en ``attempts`` y anunciado como evento del hilo: un cambio
    de proveedor silencioso que además puede gastar es peor que un loop lento.

    Raises:
        Exception: la última falla, cuando no corresponde failover o no queda candidato viable.
    """
    current_payload = payload
    for attempt in range(MAX_FAILOVER_ATTEMPTS + 1):
        failed_result = None
        if role == "product_owner" and current_payload is not payload:
            decision = (current_payload.get("metadata") or {}).get("resourceSelection") or {}
            run.product_owner_resource_decision = decision
            run.product_owner_selected_resource = decision.get("selected") or {}
        try:
            raise_if_usage_suspended(getattr(coordinator, "connection", None), current_payload)
            result = runtime.run(current_payload)
            if role == "product_owner" and isinstance(result, dict) and result.get("output") is None:
                details = result.get("runtimeResult") or {}
                status = details.get("httpStatus")
                reason = str(result.get("reason") or details.get("reason") or "")
                if isinstance(status, int):
                    reason = f"{reason} (http_status={status})"
                error = RuntimeError(reason)
                if result.get("status") in {
                    "failed",
                    "runtime_failed",
                    "runtime_unavailable",
                } and should_failover(classify_runtime_failure(error)):
                    failed_result = result
                    raise error
            return result
        except Exception as error:
            from local_control_center.process_supervision.context import ExecutionDeadlineExceeded

            if isinstance(error, ExecutionDeadlineExceeded):
                raise
            failure = classify_runtime_failure(error)
            failed_provider = str(current_payload.get("preferredRuntime") or "")
            failed_model = str(current_payload.get("model") or "")
            attempts.append(
                {
                    "attempt": attempt,
                    "providerId": failed_provider,
                    "model": failed_model,
                    "failureClass": failure.value,
                    "reason": str(redact_secrets(str(error))),
                }
            )
            if not should_failover(failure) or attempt == MAX_FAILOVER_ATTEMPTS:
                if failed_result is not None:
                    return failed_result
                raise
            replacement = coordinator._failover_replacement(
                run=run,
                payload=current_payload,
                attempts=attempts,
                provider_id=failed_provider,
                failed_model=failed_model,
                role=role,
            )
            if replacement is None:
                if failed_result is not None:
                    return failed_result
                raise
            current_payload = replacement
            coordinator._record_thread_event(
                thread_id=thread_id,
                event_type="runtime_failover",
                agent_role=role,
                payload={
                    "loopId": run.loop["id"],
                    "runtimeId": current_payload.get("preferredRuntime"),
                    "previousRuntimeId": failed_provider,
                    "failureClass": failure.value,
                    "attempt": attempt,
                    "reason": attempts[-1]["reason"],
                },
            )
    raise RuntimeError("runtime_failover_exhausted")


def raise_if_usage_suspended(connection: Any, payload: dict[str, Any]) -> None:
    """Corta el intento si su proveedor quedó sobre el umbral de uso del operador.

    La suspensión por cuota (``agents/provider_usage.py``) puede llegar a mitad de un loop: el
    siguiente intento no debe gastar más en ese proveedor. Se lanza como falla de cuota, así el
    failover existente lo excluye y elige el siguiente candidato del orden del rol. Sin conexión
    (un coordinador armado solo para ejercer el failover) no hay registro de uso que consultar.
    """
    provider_id = str(payload.get("preferredRuntime") or "").strip()
    if not provider_id or connection is None:
        return
    from local_control_center.agents.provider_usage import ProviderUsageStore

    suspension = ProviderUsageStore(connection).suspensions().get(provider_id)
    if suspension is not None:
        raise RuntimeError(
            f"usage_threshold_reached: {provider_id} is at {suspension['usedPercent']}% of its "
            f"{suspension['window']} quota (threshold {suspension['thresholdPercent']}%), "
            f"suspended until {suspension['until']}"
        )
