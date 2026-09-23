"""Roles del equipo de runtimes por hilo: elegibilidad por contrato y reparto automático determinista.

Funciones puras: no leen la base ni el reloj. La elegibilidad llama a los mismos predicados que usa
cada runner (ProductOwner, Developer, Architect, Security) y exige además la capacidad que
``AIResourceManager`` pide al rol del scheduler, para que el panel nunca ofrezca un runtime que el
ejecutor o el gestor rechazarían después. El reparto prioriza CLI para desarrollo, luego el orden
global de runtimes y por último el ``provider_id`` alfabético, así el mismo conjunto da siempre el
mismo resultado. Solo PO y Developer son obligatorios: Arquitecto y Seguridad pueden quedar sin
asignar (el Arquitecto no corre y Seguridad queda en sus scanners deterministas).

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_control_center.agents.architect_agent_contract import is_architect_runtime
from local_control_center.agents.developer_agent_contract import (
    DEVELOPER_AGENT_CLI_RUNTIMES,
    is_developer_runtime,
)
from local_control_center.agents.product_owner_agent_contract import is_product_owner_runtime
from local_control_center.agents.security_agent_contract import is_security_model_runtime

TEAM_ROLES = ("product_owner", "developer", "architect", "security")
REQUIRED_TEAM_ROLES = ("product_owner", "developer")
OPTIONAL_TEAM_ROLES = ("architect", "security")
ASSIGNMENT_ORDER = ("developer", "product_owner", "architect", "security")
_DIRECT_TEAM_ROLES = frozenset({"product_owner", "developer", "architect"})
_CODE_CAPABILITIES = frozenset({"code_edit", "issue_to_patch", "code"})
_REVIEW_CAPABILITIES = frozenset({"code_review", "review"})
_CONTRACT_EXECUTABILITY = {
    "executable": True,
    "canRunPrompt": True,
    "canEditWorkspace": True,
    "productOwnerExecutable": True,
}
"""La ejecutabilidad la demuestra ``models.validate_runtime``; aquí solo se evalúa el contrato."""


@dataclass(frozen=True)
class RuntimeFacts:
    """Hechos de un runtime configurado que deciden su elegibilidad y su rango en el reparto."""

    provider_id: str
    label: str
    kind: str
    eligible_roles: tuple[str, ...]
    policy_denied_reason: str | None = None
    """Causa de ``runtime_policy_decision`` si el proyecto deniega el runtime; ``None`` si lo permite."""


def eligible_team_roles(runtime_status: Mapping[str, Any]) -> tuple[str, ...]:
    """Roles del equipo que el runtime puede cumplir: predicado real del runner ∧ capacidad del rol.

    ``runtime_status`` es el dict de ``RuntimeStatusService`` (``id``, ``providerFamily``,
    ``capabilities``, ``models``). Developer de modelo exige además una capacidad de código y
    Seguridad, además del ``chat`` que exige su runner, una de revisión (``code_review``/``review``),
    porque ``AIResourceManager`` las pide a esos roles del scheduler.
    """
    capabilities = {str(item).strip().lower() for item in runtime_status.get("capabilities") or []}
    view = {**runtime_status, **_CONTRACT_EXECUTABILITY, "capabilities": sorted(capabilities)}
    is_cli = str(view.get("id") or "") in DEVELOPER_AGENT_CLI_RUNTIMES
    roles: list[str] = []
    if is_product_owner_runtime(view):
        roles.append("product_owner")
    if is_developer_runtime(view) and (is_cli or capabilities & _CODE_CAPABILITIES):
        roles.append("developer")
    if is_architect_runtime(view):
        roles.append("architect")
    if is_security_model_runtime(view) and capabilities & _REVIEW_CAPABILITIES:
        roles.append("security")
    return tuple(roles)


def team_role_for(role: str, *, kind: str = "", capabilities: Iterable[str] = ()) -> str | None:
    """Traduce un rol del scheduler (o el rol de failover) al rol del equipo que lo gobierna.

    Devuelve ``None`` para roles sin asignación propia (aido_lead, qa_engineer, technical_lead...):
    esos quedan confinados al conjunto completo del hilo, no a un runtime único.
    """
    normalized = str(role or "").strip().lower()
    caps = {str(item).strip().lower() for item in capabilities}
    if normalized in _DIRECT_TEAM_ROLES:
        return normalized
    if normalized.startswith("security") or "security_review" in caps:
        return "security"
    if str(kind or "").strip().lower() == "build" or "code_edit" in caps:
        return "developer"
    return None


def auto_assign_roles(runtimes: Sequence[RuntimeFacts], runtime_order: Sequence[str] = ()) -> dict[str, str]:
    """Reparte los roles entre los runtimes dados; un rol sin elegibles queda fuera del resultado.

    Cada rol toma el primer runtime elegible aún no usado; si todos se usaron, recicla desde el
    primero del ranking (CLI primero, luego ``runtime_order``, luego ``provider_id``).
    """
    order = {provider_id: index for index, provider_id in enumerate(runtime_order)}
    ranked = sorted(
        runtimes,
        key=lambda item: (item.kind != "cli", order.get(item.provider_id, len(order)), item.provider_id),
    )
    used: list[str] = []
    assignment: dict[str, str] = {}
    for role in ASSIGNMENT_ORDER:
        eligible = [item.provider_id for item in ranked if role in item.eligible_roles]
        if not eligible:
            continue
        chosen = next((provider_id for provider_id in eligible if provider_id not in used), eligible[0])
        assignment[role] = chosen
        if chosen not in used:
            used.append(chosen)
    return assignment


def missing_required_roles(role_runtimes: Mapping[str, str]) -> list[str]:
    """Roles obligatorios (PO y Developer) sin runtime asignado; los opcionales nunca faltan."""
    return [role for role in REQUIRED_TEAM_ROLES if not str(role_runtimes.get(role) or "").strip()]
