"""Selección de recursos de IA por rol: política, aprobaciones confiables y recurso de ejecución.

Fase del pipeline extraída de ``coordinator.py``: cada función recibe el coordinator y resuelve qué
proveedor/modelo atiende cada rol del run —Product Owner y equipo— a partir de la política mutable del
rol, el orden de runtimes del hilo y las aprobaciones de recurso cuya procedencia es confiable
(remediación, feedback o continuación); también traduce la decisión al runtime concreto con que corren
el Developer, el SecurityAgent y el ArchitectAgent. ``observe_resource_decision`` se resuelve en el
módulo del coordinator para que el parcheo de los tests siga aplicando.

@author Rodrigo Mason
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.developer_agent_contract import (
    DEVELOPER_AGENT_CLI_RUNTIMES,
    DEVELOPER_AGENT_MODEL_RUNTIMES,
)
from local_control_center.agents.product_owner_agent_contract import (
    PRODUCT_OWNER_AGENT_CLI_RUNTIMES,
    PRODUCT_OWNER_AGENT_ID,
    PRODUCT_OWNER_AGENT_MODEL_RUNTIMES,
    PRODUCT_OWNER_AGENT_RUNTIME_ORDER,
)
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team.configuration import (
    assigned_runtime,
    global_role_order,
    global_role_order_is_explicit,
    global_team_of,
    restrict_to_allowlist,
    role_allowlist,
    role_model_pins,
    role_routing_preferences,
    runtime_team_of,
)
from local_control_center.runtime_team.facts import role_capability_hint
from local_control_center.runtime_team.roles import team_role_for
from local_control_center.shared.redaction import redact_secrets

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator

__all__ = [
    "architect_execution_resource",
    "assigned_developer_execution_resource",
    "developer_assignment_blocker",
    "developer_execution_resource",
    "developer_execution_resource_mapping_blockers",
    "developer_resource_record",
    "developer_runtime_id_for_resource_selection",
    "has_trusted_resource_approval_metadata",
    "is_model_runtime_provider",
    "jev_provider_walk",
    "persisted_runtime_order",
    "product_owner_resource_selection",
    "product_owner_runtime_id_for_resource_selection",
    "record_assigned_provider_skipped",
    "resource_decision_with_approval_override",
    "resource_role_policy",
    "sanitize_untrusted_resource_approval_metadata",
    "security_execution_resource",
    "team_resource_request",
    "team_schedule_with_resource_decisions",
    "trusted_feedback_resource_approval_marker",
    "trusted_remediation_resource_approval_marker",
]


def resource_role_policy(
    coordinator: ProductLoopCoordinator,
    role: str,
    *,
    fallback_to_developer: bool = True,
) -> dict[str, Any]:
    """Normaliza la política mutable del rol para que ResourceManager la aplique fail-closed.

    ``requiresApprovalOverUsd`` es el umbral premium: por encima de él una selección con costo
    conocido exige aprobación humana. Espeja la precedencia del ``ModelRouter`` (umbral explícito
    y, si no lo hay, el cap por tarea). Sin política registrada se asume lo más restrictivo.
    """
    roles = [role]
    if fallback_to_developer and role != "developer":
        roles.append("developer")
    for policy_role in roles:
        try:
            policy = coordinator.routing_profiles.get_role_policy(policy_role)
        except KeyError:
            continue
        threshold = policy.get("requiresApprovalOverUsd")
        if threshold is None:
            threshold = policy.get("maxCostPerTaskUsd")
        preferred_resources = [
            preference
            for preference in [
                *(policy.get("preferred") or []),
                *(policy.get("fallback") or []),
                *(policy.get("escalation") or []),
            ]
            if isinstance(preference, dict)
        ]
        preferred_provider_ids: list[str] = []
        for preference in preferred_resources:
            provider_id = str(preference.get("provider") or "").strip()
            if provider_id and provider_id not in preferred_provider_ids:
                preferred_provider_ids.append(provider_id)
        return {
            "rolePolicyId": str(policy.get("id") or policy_role),
            "routingProfileId": str(policy.get("routingProfileId") or ""),
            "freeTierOnly": str(policy.get("routingProfileId") or "") == "free_tier",
            "allowUnknownCost": bool(policy.get("allowUnknownCost", False)),
            "requireApprovalForUnknownCost": bool(policy.get("requireApprovalForUnknownCost", True)),
            "requiresApprovalOverUsd": float(threshold) if threshold is not None else None,
            "maxTokensPerRun": int(policy.get("maxTokensPerRun") or 0) or None,
            "allowRemote": bool(policy.get("allowRemote", False)),
            "allowLocal": bool(policy.get("allowLocal", False)),
            "allowCli": bool(policy.get("allowCli", False)),
            "allowApi": bool(policy.get("allowApi", False)),
            "preferredProviderIds": preferred_provider_ids,
            "preferredResources": preferred_resources,
            "blockedResources": [item for item in policy.get("blocked") or [] if isinstance(item, dict)],
        }
    return {
        "rolePolicyId": None,
        "routingProfileId": None,
        "freeTierOnly": False,
        "allowUnknownCost": False,
        "requireApprovalForUnknownCost": True,
        "requiresApprovalOverUsd": None,
        "maxTokensPerRun": None,
        "allowRemote": False,
        "allowLocal": False,
        "allowCli": False,
        "allowApi": False,
        "preferredProviderIds": [],
        "preferredResources": [],
        "blockedResources": [],
    }


def trusted_remediation_resource_approval_marker(
    coordinator: ProductLoopCoordinator,
    *,
    request_meta: dict[str, Any],
    source_loop: dict[str, Any],
    expected_action_type: str,
) -> bool:
    """Indica si la remediación referida en ``request_meta`` respalda la aprobación de recursos del loop origen."""
    remediation_id = str(request_meta.get("remediationActionId") or "").strip()
    if not remediation_id:
        return False
    try:
        action = RemediationActionsRepository(coordinator.connection).get(remediation_id)
    except KeyError:
        return False
    return (
        action["projectId"] == source_loop["projectId"]
        and action["loopId"] == source_loop["id"]
        and action["actionType"] == expected_action_type
        and action["status"] in {"pending", "resolved"}
    )


def trusted_feedback_resource_approval_marker(
    coordinator: ProductLoopCoordinator, *, request_meta: dict[str, Any], source_loop: dict[str, Any]
) -> bool:
    """Indica si el feedback ``continue`` aplicado sobre el loop origen respalda la aprobación de recursos."""
    feedback_id = str(request_meta.get("feedbackId") or "").strip()
    if not feedback_id:
        return False
    try:
        feedback = coordinator.repository.get_feedback(feedback_id)
    except KeyError:
        return False
    return (
        feedback["projectId"] == source_loop["projectId"]
        and feedback["loopId"] == source_loop["id"]
        and feedback["action"] == "continue"
        and feedback["status"] == "applied"
    )


def has_trusted_resource_approval_metadata(
    coordinator: ProductLoopCoordinator, request_meta: dict[str, Any]
) -> bool:
    """Indica si ``approvedResourceSelections`` del request proviene de una fuente confiable del loop origen."""
    approvals = request_meta.get("approvedResourceSelections")
    if not isinstance(approvals, list) or not approvals:
        return False
    source_loop_id = coordinator._source_resource_approval_loop_id(request_meta)
    if not source_loop_id:
        return False
    try:
        source_loop = coordinator.repository.get_loop(source_loop_id)
    except KeyError:
        return False
    source_durable = coordinator._durable_run_context(source_loop)
    resource_approval = (
        source_durable.get("resourceApproval")
        if isinstance(source_durable.get("resourceApproval"), dict)
        else {}
    )
    approved_selections = resource_approval.get("approvedResourceSelections")
    if resource_approval.get("status") != "approved" or not isinstance(approved_selections, list):
        return False
    if not all(
        isinstance(approval, dict)
        and any(
            isinstance(approved, dict) and coordinator._same_resource_selection(approved, approval)
            for approved in approved_selections
        )
        for approval in approvals
    ):
        return False
    if request_meta.get("retryOfLoopId"):
        return coordinator._trusted_remediation_resource_approval_marker(
            request_meta=request_meta,
            source_loop=source_loop,
            expected_action_type="retry_loop",
        )
    if request_meta.get("planOnlyOfLoopId"):
        return coordinator._trusted_remediation_resource_approval_marker(
            request_meta=request_meta,
            source_loop=source_loop,
            expected_action_type="continue_plan_only",
        )
    if request_meta.get("continueOfLoopId"):
        return coordinator._trusted_feedback_resource_approval_marker(
            request_meta=request_meta,
            source_loop=source_loop,
        )
    return False


def sanitize_untrusted_resource_approval_metadata(
    coordinator: ProductLoopCoordinator, request_meta: dict[str, Any]
) -> dict[str, Any]:
    """Descarta ``approvedResourceSelections`` sin procedencia confiable y deja constancia de cuántas se ignoraron."""
    if "approvedResourceSelections" not in request_meta:
        return request_meta
    if has_trusted_resource_approval_metadata(coordinator, request_meta):
        return request_meta
    approvals = request_meta.get("approvedResourceSelections")
    sanitized = dict(request_meta)
    sanitized.pop("approvedResourceSelections", None)
    if isinstance(approvals, list) and approvals:
        sanitized["ignoredApprovedResourceSelectionCount"] = len(approvals)
        sanitized["ignoredApprovedResourceSelectionReason"] = (
            "approvedResourceSelections require remediation, retry, plan-only, or continuation provenance."
        )
    return sanitized


def resource_decision_with_approval_override(
    coordinator: ProductLoopCoordinator,
    *,
    role: str,
    decision: dict[str, Any],
    request_meta: dict[str, Any],
) -> dict[str, Any]:
    """Marca la decisión como aprobada si el operador ya aprobó esa selección para el rol."""
    if not bool(decision.get("approvalRequired")):
        return decision
    approval = coordinator._approved_resource_selection_for(
        role=role,
        selected=decision.get("selected") if isinstance(decision.get("selected"), dict) else None,
        request_meta=request_meta,
    )
    if approval is None:
        return decision
    policy_result = dict(decision.get("policyResult") or {})
    policy_result["approvalOverride"] = {"approved": True, **approval}
    return {
        **decision,
        "approvalSatisfied": True,
        "decisionReason": (
            f"{decision.get('decisionReason') or 'Selected AI resource.'} "
            "Operator approved this exact resource selection for retry."
        ),
        "policyResult": policy_result,
    }


def team_schedule_with_resource_decisions(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    request_meta: dict[str, Any],
    team_schedule: dict[str, Any],
    agent_tasks: list[dict[str, Any]],
    runtime_risk_review_id: str | None = None,
    product_owner_selected_resource: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Asigna a cada rol del equipo su decisión de recurso y devuelve el schedule enriquecido y los bloqueos."""
    from local_control_center.product_loop import coordinator as coordinator_module

    manager = AIResourceManager(coordinator.connection)
    privacy_level = coordinator._resource_privacy_level(request_meta)
    enriched_roles: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    local_affinity: dict[str, str] = {}
    thread_has_team = runtime_team_of(request_meta) is not None
    po_provider_id = str((product_owner_selected_resource or {}).get("providerId") or "").strip() or None
    po_model = str((product_owner_selected_resource or {}).get("model") or "").strip()
    if not thread_has_team and po_provider_id and po_model:
        local_affinity[po_provider_id] = po_model
    for role_plan in team_schedule["roles"]:
        role = str(role_plan["role"])
        task = coordinator._task_for_assignment(role, agent_tasks)
        resource_policy = coordinator._resource_role_policy(role)
        routing_started = time.perf_counter()
        select_kwargs = {
            "record": True,
            "allow_decision_inference": runtime_risk_review_id is None,
            **({"runtime_risk_review_id": runtime_risk_review_id} if runtime_risk_review_id else {}),
        }
        team_role = team_role_for(
            role, kind=str(role_plan.get("kind") or ""), capabilities=role_plan.get("capabilities") or []
        )
        walk = (
            coordinator._jev_provider_walk(project_id, request_meta, team_role)
            if runtime_risk_review_id is None
            else []
        )
        decision = coordinator._select_walking_providers(
            manager,
            lambda allowed, role_plan=role_plan: coordinator._team_resource_request(
                project_id=project_id,
                loop_id=loop_id,
                request_meta=request_meta,
                team_schedule=team_schedule,
                role_plan=role_plan,
                agent_tasks=agent_tasks,
                local_model_affinity=local_affinity,
                product_owner_provider_id=po_provider_id,
                allowed_override=allowed,
            ),
            None,
            walk,
            **select_kwargs,
        )
        if (
            runtime_risk_review_id is None
            and not thread_has_team
            and po_provider_id
            and not decision.get("candidates")
            and global_team_of(request_meta) is None
        ):
            widened_request = coordinator._team_resource_request(
                project_id=project_id,
                loop_id=loop_id,
                request_meta=request_meta,
                team_schedule=team_schedule,
                role_plan=role_plan,
                agent_tasks=agent_tasks,
                local_model_affinity=local_affinity,
                product_owner_provider_id=None,
            )
            decision = manager.select_resource(widened_request, **select_kwargs)
            coordinator._record_loop_event(
                project_id=project_id,
                event_type="product_loop.runtime_allowlist_widened",
                loop_id=loop_id,
                payload={
                    "role": role,
                    "taskId": task["id"],
                    "productOwnerProviderId": po_provider_id,
                    "reason": "no_candidates_for_product_owner_provider",
                },
                thread_id=thread_id,
            )
        decision = resource_decision_with_approval_override(
            coordinator,
            role=role,
            decision=decision,
            request_meta=request_meta,
        )
        coordinator._record_assigned_provider_skipped(
            thread_id=thread_id,
            loop_id=loop_id,
            request_meta=request_meta,
            team_role=team_role,
            role=role,
            decision=decision,
        )
        coordinator_module.observe_resource_decision(
            coordinator.connection,
            decision=decision,
            project_id=project_id,
            execution_id=loop_id,
            task_id=task["id"],
            risk=str(team_schedule.get("risk") or "medium"),
            privacy_mode="local_only"
            if request_meta.get("forceLocal")
            or not resource_policy["allowRemote"]
            or privacy_level not in {"public", "remote_allowed"}
            else "metadata_only",
            routing_latency_ms=(time.perf_counter() - routing_started) * 1000,
        )
        public_decision = coordinator._public_resource_decision(decision)
        selected_resource = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
        for entry in (decision.get("policyResult") or {}).get("localModelSelections") or []:
            if isinstance(entry, dict) and entry.get("runtimeId") == selected_resource.get("providerId"):
                local_affinity.setdefault(str(entry["runtimeId"]), str(entry.get("model") or ""))
        enriched_roles.append({**role_plan, "resourceDecision": public_decision})
        if decision.get("selected") is None:
            reason = decision.get("decisionReason") or "AIResourceManager did not select a resource."
            hint, capability_gaps = role_capability_hint(
                coordinator.connection,
                team_role=team_role,
                rejected=decision.get("rejected") or [],
                project_id=project_id,
            )
            blockers.append(
                {
                    "role": role,
                    "taskId": task["id"],
                    "reason": f"{reason} {hint}" if hint else reason,
                    "decision": public_decision,
                    **({"capabilityGaps": capability_gaps} if capability_gaps else {}),
                }
            )
        elif coordinator._resource_decision_has_pending_approval(decision):
            blockers.append(
                {
                    "role": role,
                    "taskId": task["id"],
                    "reason": "AIResourceManager selected a resource that requires approval before execution.",
                    "decision": public_decision,
                }
            )
    enriched = {
        **team_schedule,
        "roles": enriched_roles,
        "summary": {
            **dict(team_schedule.get("summary") or {}),
            "resourceDecisionCount": len(enriched_roles),
            "resourceDecisionBlockedCount": len(blockers),
        },
    }
    return enriched, blockers


def record_assigned_provider_skipped(
    coordinator: ProductLoopCoordinator,
    *,
    thread_id: str | None,
    loop_id: str,
    request_meta: dict[str, Any],
    team_role: str | None,
    role: str,
    decision: dict[str, Any],
) -> None:
    """Evento ``runtime_failover`` cuando la selección no pudo usar el proveedor asignado del rol.

    Spec §4.8: con equipo de IA global, si el primero del orden del rol no puede servir (inactivo,
    sin validación o sin candidatos) y se elige el siguiente, el hilo lo dice. No aplica con equipo
    por hilo (allowlist de un proveedor) ni sin snapshot global.
    """
    if runtime_team_of(request_meta) is not None or global_team_of(request_meta) is None:
        return
    order = global_role_order(request_meta, team_role)
    selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
    runtime_id = str(selected.get("providerId") or "").strip()
    if not order or not runtime_id or runtime_id == order[0]:
        return
    coordinator._record_thread_event(
        thread_id=thread_id,
        event_type="runtime_failover",
        agent_role=role,
        payload={
            "loopId": loop_id,
            "runtimeId": runtime_id,
            "previousRuntimeId": order[0],
            "failureClass": "assigned_unavailable",
            "attempt": 0,
            "reason": (
                f"The assigned provider {order[0]} could not serve the role; "
                f"{runtime_id} was selected from the role's order."
            ),
        },
    )


def jev_provider_walk(
    coordinator: ProductLoopCoordinator, project_id: str, request_meta: dict[str, Any], team_role: str | None
) -> list[str]:
    """Orden a recorrer de a un proveedor cuando Jev elige el runtime y el orden es automático.

    Con Jev en ``runtime_selection`` las preferencias de ruteo no aplican y un allowlist con varios
    proveedores reabre la ambigüedad (``confidence_below_threshold``) que la herencia del PO evitaba.
    El orden automático trae a todos los elegibles activos sin que el operador los haya elegido, así
    que se prueba cada proveedor en orden (también con ``automatic_fallback``, cuando la lista manual
    del rol quedó entera apagada). Un orden explícito del operador no recorre: el spec acepta la
    ambigüedad si el operador deja varios proveedores. Vacío si no aplica.
    """
    from local_control_center.product_loop import coordinator as coordinator_module

    if runtime_team_of(request_meta) is not None or global_team_of(request_meta) is None:
        return []
    if global_role_order_is_explicit(request_meta, team_role):
        return []
    if not coordinator_module.resolve_config(coordinator.connection, project_id).selects_runtime:
        return []
    return global_role_order(request_meta, team_role)


def team_resource_request(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    request_meta: dict[str, Any],
    team_schedule: dict[str, Any],
    role_plan: dict[str, Any],
    agent_tasks: list[dict[str, Any]],
    local_model_affinity: dict[str, str] | None = None,
    product_owner_provider_id: str | None = None,
    allowed_override: list[str] | None = None,
) -> AIResourceRequest:
    """Arma el ``AIResourceRequest`` de un rol del equipo según su política y el orden de runtimes del hilo."""
    role = str(role_plan["role"])
    profile = coordinator._profile_by_role(project_id).get(role) or {}
    task = coordinator._task_for_assignment(role, agent_tasks)
    policy = coordinator._resource_role_policy(role)
    team_role = team_role_for(
        role, kind=str(role_plan.get("kind") or ""), capabilities=role_plan.get("capabilities") or []
    )
    order, order_wildcards = role_routing_preferences(request_meta, team_role)
    preferred_provider_ids = [
        *order,
        *(provider_id for provider_id in policy["preferredProviderIds"] if provider_id not in order),
    ]
    preferred_resources = [*order_wildcards, *policy["preferredResources"]]
    return AIResourceRequest(
        project_id=project_id,
        workflow_run_id=loop_id,
        agent_id=str(profile.get("id") or role),
        agent_profile_id=profile.get("id"),
        task_id=task["id"],
        task_type=f"{role}.{role_plan.get('kind') or 'reason'}",
        risk_level=str(team_schedule.get("risk") or "medium"),
        routing_policy="economy" if policy["freeTierOnly"] else str(team_schedule.get("mode") or "balanced"),
        context_tokens_estimate=coordinator._resource_context_tokens_estimate(request_meta),
        required_capabilities=coordinator._resource_required_capabilities(role_plan),
        allowed_provider_ids=allowed_override
        if allowed_override is not None
        else role_allowlist(request_meta, team_role, product_owner_provider_id=product_owner_provider_id),
        local_model_pins=role_model_pins(request_meta, team_role),
        local_model_affinity=dict(local_model_affinity or {}),
        privacy_level=coordinator._resource_privacy_level(request_meta),
        budget_remaining_usd=role_plan.get("budgetUsd"),
        max_tokens=role_plan.get("maxTokens"),
        preferred_provider_ids=preferred_provider_ids,
        preferred_resources=preferred_resources,
        provider_order=order,
        blocked_resources=policy["blockedResources"],
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


def is_model_runtime_provider(coordinator: ProductLoopCoordinator, provider_id: str) -> bool:
    """Indica si la cuenta corresponde a un runtime de modelo (no CLI).

    Resuelve por ``provider_family`` y no por ``provider_id``: una cuenta con id propio —por
    ejemplo un gateway ``omniroute`` de familia ``openai_compatible``— debe mapear igual que la
    cuenta homónima de la familia, tal como ya lo hace el camino del ProductOwner. Sin esto la
    selección de recursos elige el gateway y luego el loop lo rechaza por no mapear a un runtime.
    """
    if not provider_id:
        return False
    row = coordinator.connection.execute(
        "SELECT provider_family, api_format FROM provider_accounts WHERE provider_id = ?",
        (provider_id,),
    ).fetchone()
    if row is None:
        return False
    return (
        str(row["provider_family"] or "") in DEVELOPER_AGENT_MODEL_RUNTIMES
        or str(row["api_format"] or "") == "ollama"
    )


def developer_runtime_id_for_resource_selection(
    coordinator: ProductLoopCoordinator, selected: dict[str, Any]
) -> str | None:
    """Runtime del Developer que corresponde a la selección; ``None`` si no es ejecutable por el Developer."""
    allowed_runtimes = DEVELOPER_AGENT_CLI_RUNTIMES | DEVELOPER_AGENT_MODEL_RUNTIMES
    provider_id = str(selected.get("providerId") or "").strip()
    runtime_id = str(selected.get("runtime") or "").strip()
    if provider_id in allowed_runtimes or is_model_runtime_provider(coordinator, provider_id):
        return provider_id
    if runtime_id in allowed_runtimes:
        return runtime_id
    return None


def product_owner_runtime_id_for_resource_selection(
    coordinator: ProductLoopCoordinator, selected: dict[str, Any]
) -> str | None:
    """Runtime del Product Owner que corresponde a la selección; ``None`` si no es ejecutable por el PO."""
    provider_id = str(selected.get("providerId") or "").strip()
    runtime_id = str(selected.get("runtime") or "").strip()
    if provider_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        return provider_id
    row = coordinator.connection.execute(
        """
            SELECT provider_family, api_format, provider_type
            FROM provider_accounts
            WHERE provider_id = ?
            """,
        (provider_id,),
    ).fetchone()
    if row and (
        str(row["provider_family"] or "") in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
        or str(row["api_format"] or "") == "ollama"
    ):
        return provider_id
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        return runtime_id
    return None


def persisted_runtime_order(coordinator: ProductLoopCoordinator) -> list[str]:
    """Devuelve el orden de runtimes elegido por el operador, o vacío si no hay ninguno.

    Sin preferencias registradas ``get_preferences`` levanta ``KeyError``; eso no es un error
    de operación sino la instalación por defecto, así que degrada a lista vacía.
    """
    try:
        preferences = RuntimeConfigRepository(coordinator.connection).get_preferences()
    except KeyError:
        return []
    return [str(item) for item in (preferences.get("runtimeOrder") or []) if str(item).strip()]


def product_owner_resource_selection(
    coordinator: ProductLoopCoordinator,
    *,
    project_id: str,
    loop_id: str,
    task_id: str,
    request_meta: dict[str, Any],
    excluded_resources: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Selecciona el recurso del Product Owner y devuelve la decisión pública o el resultado de bloqueo."""
    from local_control_center.product_loop import coordinator as coordinator_module

    manager = AIResourceManager(coordinator.connection)
    resource_policy = coordinator._resource_role_policy(
        "product_owner",
        fallback_to_developer=False,
    )
    model_provider_rows = [
        dict(row)
        for row in coordinator.connection.execute(
            """
                SELECT provider_id, provider_family, api_format
                FROM provider_accounts
                WHERE provider_type IN ('api', 'gateway', 'local')
                ORDER BY provider_id ASC
                """
        ).fetchall()
        if str(row["provider_family"] or "") in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
        or str(row["api_format"] or "") == "ollama"
    ]
    model_provider_ids = {str(row["provider_id"]) for row in model_provider_rows}
    allowed_provider_ids = sorted(PRODUCT_OWNER_AGENT_CLI_RUNTIMES | model_provider_ids)
    allowed_provider_ids = restrict_to_allowlist(
        allowed_provider_ids, role_allowlist(request_meta, "product_owner")
    )
    po_order, po_wildcards = role_routing_preferences(request_meta, "product_owner")
    preferred_provider_ids: list[str] = []
    ordered_contract_providers: list[str] = []
    for runtime_family in PRODUCT_OWNER_AGENT_RUNTIME_ORDER:
        if runtime_family in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
            ordered_contract_providers.append(runtime_family)
            continue
        ordered_contract_providers.extend(
            str(row["provider_id"])
            for row in model_provider_rows
            if str(row["provider_family"] or "") == runtime_family
            or (runtime_family == "ollama" and str(row["api_format"] or "") == "ollama")
        )
    for provider_id in [
        *po_order,
        *persisted_runtime_order(
            coordinator,
        ),
        *resource_policy["preferredProviderIds"],
        *ordered_contract_providers,
    ]:
        if provider_id in allowed_provider_ids and provider_id not in preferred_provider_ids:
            preferred_provider_ids.append(provider_id)
    preferred_resources = [
        *po_wildcards,
        *(
            {"provider": provider_id, "model": ""}
            for provider_id in persisted_runtime_order(
                coordinator,
            )
        ),
        *resource_policy["preferredResources"],
    ]

    def product_owner_request(allowed: list[str] | None) -> AIResourceRequest:
        return AIResourceRequest(
            project_id=project_id,
            workflow_run_id=loop_id,
            agent_id=PRODUCT_OWNER_AGENT_ID,
            agent_profile_id=(coordinator._profile_by_role(project_id).get("product_owner") or {}).get("id"),
            task_id=task_id,
            task_type="product_owner.discovery",
            risk_level=str(request_meta.get("risk") or "medium"),
            routing_policy=(
                "economy" if resource_policy["freeTierOnly"] else coordinator._team_mode(request_meta)
            ),
            context_tokens_estimate=coordinator._resource_context_tokens_estimate(request_meta),
            required_capabilities=["chat"],
            allowed_provider_ids=allowed,
            local_model_pins=({} if excluded_resources else role_model_pins(request_meta, "product_owner")),
            excluded_resources=excluded_resources or [],
            preferred_provider_ids=preferred_provider_ids,
            preferred_resources=preferred_resources,
            provider_order=po_order,
            blocked_resources=resource_policy["blockedResources"],
            context_token_limit=resource_policy["maxTokensPerRun"],
            role_policy_id=resource_policy["rolePolicyId"],
            allow_remote=resource_policy["allowRemote"],
            allow_local=resource_policy["allowLocal"],
            allow_cli=resource_policy["allowCli"],
            allow_api=resource_policy["allowApi"],
            free_tier_only=resource_policy["freeTierOnly"],
            privacy_level=coordinator._resource_privacy_level(request_meta),
            allow_unknown_cost=resource_policy["allowUnknownCost"],
            require_approval_for_unknown_cost=resource_policy["requireApprovalForUnknownCost"],
            require_approval_over_usd=resource_policy["requiresApprovalOverUsd"],
        )

    routing_started = time.perf_counter()
    walk = [
        provider_id
        for provider_id in coordinator._jev_provider_walk(project_id, request_meta, "product_owner")
        if provider_id in allowed_provider_ids
    ]
    decision = coordinator._select_walking_providers(
        manager,
        product_owner_request,
        allowed_provider_ids,
        walk,
        record=True,
        allow_decision_inference=True,
    )
    decision = resource_decision_with_approval_override(
        coordinator,
        role="product_owner",
        decision=decision,
        request_meta=request_meta,
    )
    coordinator_module.observe_resource_decision(
        coordinator.connection,
        decision=decision,
        project_id=project_id,
        execution_id=loop_id,
        task_id=task_id,
        risk=str(request_meta.get("risk") or "medium"),
        privacy_mode="local_only"
        if request_meta.get("forceLocal")
        or not resource_policy["allowRemote"]
        or coordinator._resource_privacy_level(request_meta) not in {"public", "remote_allowed"}
        else "metadata_only",
        routing_latency_ms=(time.perf_counter() - routing_started) * 1000,
    )
    public_decision = coordinator._public_resource_decision(decision)
    selected = public_decision.get("selected") if isinstance(public_decision.get("selected"), dict) else None
    runtime_id = coordinator._product_owner_runtime_id_for_resource_selection(selected or {})
    if selected is None:
        return public_decision, {
            "role": "product_owner",
            "taskId": task_id,
            "reason": decision.get("decisionReason") or "AIResourceManager did not select a resource.",
            "decision": public_decision,
        }
    if coordinator._resource_decision_has_pending_approval(public_decision):
        return public_decision, {
            "role": "product_owner",
            "taskId": task_id,
            "reason": "AIResourceManager selected a resource that requires approval before execution.",
            "decision": public_decision,
        }
    if runtime_id is None:
        return public_decision, {
            "role": "product_owner",
            "taskId": task_id,
            "reason": "Selected AI resource does not map to a ProductOwnerAgent runtime.",
            "decision": public_decision,
        }
    return public_decision, None


def developer_execution_resource(
    coordinator: ProductLoopCoordinator,
    team_schedule: dict[str, Any],
    request_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recurso con que ejecuta el Developer: el asignado al hilo o el del rol de build/review del schedule."""
    assigned = assigned_runtime(request_meta, "developer")
    if assigned:
        return assigned_developer_execution_resource(coordinator, team_schedule, assigned)
    roles = [
        role_plan
        for role_plan in team_schedule.get("roles") or []
        if isinstance((role_plan.get("resourceDecision") or {}).get("selected"), dict)
        and (role_plan.get("resourceDecision") or {}).get("selected")
    ]
    execution_roles = [
        role_plan
        for role_plan in roles
        if role_plan.get("kind") in {"build", "review"}
        or "code_edit" in (role_plan.get("capabilities") or [])
    ]
    for role_plan in execution_roles or roles:
        decision = role_plan.get("resourceDecision") or {}
        selected = decision.get("selected") or {}
        preferred_runtime = coordinator._developer_runtime_id_for_resource_selection(selected)
        if not preferred_runtime:
            continue
        return developer_resource_record(role_plan, decision, selected, preferred_runtime)
    return {}


def developer_resource_record(
    role_plan: dict[str, Any], decision: dict[str, Any], selected: dict[str, Any], preferred_runtime: str
) -> dict[str, Any]:
    """Recurso de ejecución del developer, redactado, a partir de una decisión real del schedule."""
    return redact_secrets(
        {
            "role": role_plan.get("role"),
            "providerId": selected.get("providerId"),
            "model": selected.get("model"),
            "runtime": selected.get("runtime"),
            "preferredRuntime": preferred_runtime,
            "decisionReason": decision.get("decisionReason"),
            "estimatedCostUsd": decision.get("estimatedCostUsd"),
            "usageStatus": decision.get("usageStatus"),
        }
    )


def assigned_developer_execution_resource(
    coordinator: ProductLoopCoordinator, team_schedule: dict[str, Any], assigned: str
) -> dict[str, Any]:
    """Recurso del developer asignado por el hilo, solo si AIResourceManager lo seleccionó de verdad.

    Sin una decisión real (política, costo o aprobación la dejaron fuera) devuelve ``{}`` y
    ``_developer_assignment_blocker`` bloquea: nunca se fabrica una selección que salte los
    blockers del gestor ni se usa otro runtime del schedule. Solo cuentan los roles que el equipo
    gobierna como developer: otro rol (aido_lead, architect) pudo elegir el mismo runtime con otro
    modelo o presupuesto y no debe suplantar la decisión de desarrollo.
    """
    for role_plan in team_schedule.get("roles") or []:
        team_role = team_role_for(
            str(role_plan.get("role") or ""),
            kind=str(role_plan.get("kind") or ""),
            capabilities=role_plan.get("capabilities") or [],
        )
        if team_role != "developer":
            continue
        decision = role_plan.get("resourceDecision") or {}
        selected = decision.get("selected") or {}
        if not isinstance(selected, dict) or str(selected.get("providerId") or "").strip() != assigned:
            continue
        preferred_runtime = coordinator._developer_runtime_id_for_resource_selection(selected)
        if not preferred_runtime:
            continue
        return developer_resource_record(role_plan, decision, selected, preferred_runtime)
    return {}


def developer_assignment_blocker(
    request_meta: dict[str, Any] | None,
    execution_resource: dict[str, Any],
) -> dict[str, Any] | None:
    """Bloqueo cuando el hilo asignó un developer que AIResourceManager no seleccionó para ejecutar."""
    assigned = assigned_runtime(request_meta, "developer")
    if not assigned or str(execution_resource.get("providerId") or "").strip() == assigned:
        return None
    return {
        "role": "developer",
        "reason": (
            f"The thread runtime team assigns {assigned} to development, but AIResourceManager did not "
            "select it for execution; AIDO will not switch to another runtime on its own."
        ),
        "decision": {},
    }


def security_execution_resource(
    coordinator: ProductLoopCoordinator,
    team_schedule: dict[str, Any],
    request_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Devuelve el runtime de modelo que el schedule eligió para el rol de seguridad, si lo hay.

    El análisis de modelo del SecurityAgent es asistencia opcional sobre veredictos que siguen
    siendo deterministas, así que solo se activa cuando la selección de recursos del rol ya
    resolvió un runtime de modelo ejecutable; sin él, el agente corre solo sus scanners.
    Con equipo de runtimes en el hilo, solo el runtime asignado a seguridad puede analizar.
    """
    team_configured = runtime_team_of(request_meta) is not None
    assigned = assigned_runtime(request_meta, "security")
    for role_plan in team_schedule.get("roles") or []:
        capabilities = set(role_plan.get("capabilities") or [])
        role = str(role_plan.get("role") or "")
        if "security_review" not in capabilities and not role.startswith("security"):
            continue
        selected = (role_plan.get("resourceDecision") or {}).get("selected") or {}
        provider_id = str(selected.get("providerId") or "").strip()
        if not is_model_runtime_provider(coordinator, provider_id):
            continue
        if team_configured and provider_id != assigned:
            continue
        return {"preferredRuntime": provider_id, "model": selected.get("model")}
    return {}


def architect_execution_resource(
    coordinator: ProductLoopCoordinator,
    team_schedule: dict[str, Any],
    request_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Devuelve el runtime y el modelo del Architect según el ``resourceDecision`` de su rol.

    Solo toma el modelo de un runtime de modelo (no CLI), así un runtime local recibe el
    modelo resuelto por el ruteo en vez de quedar sin modelo. Con equipo sellado solo vale la
    selección del runtime asignado al architect; sin selección utilizable conserva el
    comportamiento previo (el runtime asignado, si lo hay).
    """
    assigned = assigned_runtime(request_meta, "architect")
    for role_plan in (team_schedule or {}).get("roles") or []:
        if str(role_plan.get("role") or "") not in coordinator._ARCHITECT_TEAM_ROLES:
            continue
        selected = (role_plan.get("resourceDecision") or {}).get("selected") or {}
        provider_id = str(selected.get("providerId") or "").strip()
        model = str(selected.get("model") or "").strip()
        if not model or not is_model_runtime_provider(coordinator, provider_id):
            continue
        if assigned and provider_id != assigned:
            continue
        return {"preferredRuntime": provider_id, "model": model}
    return {"preferredRuntime": assigned} if assigned else {}


def developer_execution_resource_mapping_blockers(
    coordinator: ProductLoopCoordinator, team_schedule: dict[str, Any]
) -> list[dict[str, Any]]:
    """Bloqueos de los roles de ejecución cuya selección no se traduce a un runtime del Developer."""
    roles = [
        role_plan
        for role_plan in team_schedule.get("roles") or []
        if isinstance((role_plan.get("resourceDecision") or {}).get("selected"), dict)
        and (role_plan.get("resourceDecision") or {}).get("selected")
    ]
    execution_roles = [
        role_plan
        for role_plan in roles
        if role_plan.get("kind") in {"build", "review"}
        or "code_edit" in (role_plan.get("capabilities") or [])
    ]
    targets = execution_roles or roles
    if not targets:
        return []
    if any(
        coordinator._developer_runtime_id_for_resource_selection(
            (role_plan.get("resourceDecision") or {}).get("selected") or {}
        )
        for role_plan in targets
    ):
        return []
    return [
        redact_secrets(
            {
                "role": role_plan.get("role"),
                "reason": "Selected AI resource does not map to a DeveloperAgent runtime.",
                "decision": role_plan.get("resourceDecision") or {},
            }
        )
        for role_plan in targets
    ]
