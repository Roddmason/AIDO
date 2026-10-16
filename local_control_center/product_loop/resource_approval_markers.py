"""Helpers puros para decisiones de recurso: proyección pública, aprobaciones del operador y coincidencias.

No dependen del estado del coordinador; encapsulan las reglas de comparación entre una selección de
recurso, sus aprobaciones previas y las observaciones de uso del runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.shared.redaction import redact_secrets


def public_resource_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Proyección pública y redactada de una decisión de recurso."""
    return redact_secrets(
        {
            "routingDecisionId": decision.get("routingDecisionId"),
            "selected": decision.get("selected"),
            "proposal": decision.get("proposal"),
            "reviewRequired": bool(decision.get("reviewRequired")),
            "reviewerSelection": decision.get("reviewerSelection"),
            "localVsRemote": decision.get("localVsRemote"),
            "costTier": decision.get("costTier"),
            "multiModelQuorum": decision.get("multiModelQuorum"),
            "contextCompression": decision.get("contextCompression"),
            "maxTokens": decision.get("maxTokens"),
            "budgetStop": decision.get("budgetStop"),
            "approvalRequired": decision.get("approvalRequired"),
            "approvalSatisfied": bool(decision.get("approvalSatisfied")),
            "estimatedCostUsd": decision.get("estimatedCostUsd"),
            "usageStatus": decision.get("usageStatus"),
            "decisionReason": decision.get("decisionReason"),
            "scoreBreakdown": decision.get("scoreBreakdown") or {},
            "policyResult": decision.get("policyResult") or {},
            "candidates": decision.get("candidates") or [],
            "rejected": decision.get("rejected") or [],
        }
    )


def approved_resource_selection_for(
    *,
    role: str,
    selected: dict[str, Any] | None,
    request_meta: dict[str, Any],
) -> dict[str, Any] | None:
    """Aprobación explícita del operador que coincide con la selección de recurso del rol, si existe."""
    if not selected:
        return None
    approvals = request_meta.get("approvedResourceSelections")
    if not isinstance(approvals, list):
        return None
    for approval in approvals:
        if not isinstance(approval, dict):
            continue
        if str(approval.get("role") or "") != role:
            continue
        if all(
            str(approval.get(key) or "") == str(selected.get(key) or "")
            for key in ("providerId", "model", "runtime")
        ):
            return redact_secrets(approval)
    return None


def same_resource_selection(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Indica si dos selecciones de recurso coinciden en rol, proveedor, modelo y runtime."""
    return all(
        str(current.get(key) or "") == str(candidate.get(key) or "")
        for key in ("role", "providerId", "model", "runtime")
    )


def source_resource_approval_loop_id(request_meta: dict[str, Any]) -> str:
    """Id del loop de origen (retry/plan-only/continuación) del que se heredan aprobaciones."""
    return str(
        request_meta.get("retryOfLoopId")
        or request_meta.get("planOnlyOfLoopId")
        or request_meta.get("continueOfLoopId")
        or ""
    ).strip()


def resource_decision_has_pending_approval(decision: dict[str, Any]) -> bool:
    """Indica si la decisión de recurso exige una aprobación que aún no se otorgó."""
    if not bool(decision.get("approvalRequired")):
        return False
    if bool(decision.get("approvalSatisfied")):
        return False
    policy_result = decision.get("policyResult") if isinstance(decision.get("policyResult"), dict) else {}
    override = (
        policy_result.get("approvalOverride")
        if isinstance(policy_result.get("approvalOverride"), dict)
        else {}
    )
    return override.get("approved") is not True


def resource_required_capabilities(role_plan: dict[str, Any]) -> list[str]:
    """Capacidades de recurso que exige un rol del plan de equipo (``chat`` por defecto)."""
    capabilities = {str(item).strip().lower() for item in role_plan.get("capabilities") or []}
    kind = str(role_plan.get("kind") or "").strip().lower()
    required: list[str] = []
    if "code_edit" in capabilities:
        required.append("code")
    if kind == "review" or capabilities & {"security_review", "test_design", "regression", "code_review"}:
        required.append("review")
    return required or ["chat"]


def resource_context_tokens_estimate(request_meta: dict[str, Any]) -> int:
    """Estimación de tokens de contexto declarada en la metadata del pedido (0 si no hay)."""
    for key in (
        "contextTokensEstimate",
        "context_tokens_estimate",
        "estimatedTokens",
        "estimated_tokens",
    ):
        value = request_meta.get(key)
        if value is None or value == "":
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return 0


def resource_privacy_level(request_meta: dict[str, Any]) -> str:
    """Nivel de privacidad exigido para elegir recurso (``local_only`` si se fuerza local)."""
    if request_meta.get("forceLocal"):
        return "local_only"
    value = str(
        request_meta.get("privacyLevel")
        or request_meta.get("privacy_level")
        or request_meta.get("resourcePrivacyLevel")
        or "remote_allowed"
    ).strip()
    return value or "remote_allowed"


def resource_role_matches(role_plan: dict[str, Any], usage_entry: dict[str, Any]) -> bool:
    """Indica si la entrada de uso corresponde al rol del plan (sin rol declarado, coincide)."""
    entry_role = str(usage_entry.get("role") or usage_entry.get("agentRole") or "").strip()
    if not entry_role:
        return True
    return entry_role == str(role_plan.get("role") or "")


def resource_selection_matches(selected: dict[str, Any], usage_entry: dict[str, Any]) -> bool:
    """Indica si la entrada de uso es consistente con el recurso seleccionado."""
    for selected_key, entry_keys in (
        ("providerId", ("providerId", "provider_id", "provider")),
        ("model", ("model",)),
        ("runtime", ("runtime", "runtimeType", "runtime_type")),
    ):
        entry_value = next(
            (usage_entry.get(key) for key in entry_keys if usage_entry.get(key) not in {None, ""}),
            None,
        )
        if entry_value is not None and str(entry_value) != str(selected.get(selected_key)):
            return False
    return True


def select_walking_providers(
    manager: AIResourceManager,
    build_request: Any,
    allowed: list[str] | None,
    walk: list[str],
    **select_kwargs: Any,
) -> dict[str, Any]:
    """Selecciona con ``allowed``; con ``walk`` de 2+ proveedores prueba de a uno, en orden.

    Cada proveedor se sondea sin registrar (``record=False``: sin fila de ruteo ni Jev) y solo el
    primero con candidatos se selecciona de verdad; su veredicto es el de la elección determinista.
    Si ninguno tiene candidatos, se registra una única decisión sobre el orden completo para que el
    blocker de asignación tenga su evidencia.
    """
    if len(walk) < 2:
        return manager.select_resource(build_request(allowed), **select_kwargs)
    for provider_id in walk:
        probe = manager.select_resource(build_request([provider_id]), record=False)
        if probe.get("selected") is not None or probe.get("candidates"):
            return manager.select_resource(build_request([provider_id]), **select_kwargs)
    return manager.select_resource(build_request(list(walk)), **select_kwargs)


def unscheduled_agent_task_roles(
    *, agent_tasks: list[dict[str, Any]], team_schedule: dict[str, Any]
) -> list[str]:
    """Roles de las tareas del agente que el plan de equipo no programó."""
    scheduled_roles = {
        str(role_plan.get("role") or "").strip()
        for role_plan in team_schedule.get("roles") or []
        if str(role_plan.get("role") or "").strip()
    }
    task_roles = {
        str(task.get("role") or "").strip() for task in agent_tasks if str(task.get("role") or "").strip()
    }
    return sorted(role for role in task_roles if role not in scheduled_roles)
