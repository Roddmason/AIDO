"""Equipo de IA global: asignación de runtimes por rol para todos los hilos, general > proyecto.

Regla del operador: si no elige, se asigna automáticamente; si eligió, manda su elección. Solo son
candidatos los proveedores habilitados: switch propio (``provider_accounts.enabled``), interruptor de
grupo (``runtime.<cli|remote|local|ollama|nvidia>.enabled``) y política del proyecto
(``runtime_policy_decision`` y ``project.runtime.allowedProviders``).

Cada rol tiene una lista ordenada de proveedores (`team.role.<rol>`, settings con override por
proyecto). El primero es el asignado; los siguientes son fallback.

- Manual (lista configurada): se respeta el orden del operador filtrado a activos y elegibles, sin
  reordenar por frescura ni descartar lo vencido (la frescura de una elección manual la resuelven los
  gates de ejecución y el failover). Si ningún id configurado queda activo y elegible, el rol no queda
  vacío: usa el reparto automático con procedencia ``automatic_fallback`` y reporta los ids en
  ``invalid``.
- Automático (lista vacía): el reparto determinista de ``auto_assign_roles`` sobre los activos y
  elegibles, seguido del resto en el mismo orden. Ese orden prefiere los proveedores con validación
  fresca (``RUNTIME_TEAM_FRESHNESS_SECONDS``), luego el ranking de siempre (CLI, ``runtimeOrder``, id), y
  manda al final a los suspendidos por cuota (vuelven solos cuando la ventana se reinicia). La frescura
  solo reordena: nunca bloquea al equipo global.

``technical_lead`` y ``researcher`` heredan del PO cuando quedan vacíos. Los ids que no son activos ni
elegibles se reportan (`invalid`) y no rompen la resolución. Todo se lee de SQLite (sin sondas con
``offline``): el sellado corre dentro de la transacción del envío.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.settings.registry import GLOBAL_TEAM_ROLE_KEYS
from local_control_center.settings.repository import UNSET, SettingsRepository
from local_control_center.settings.resolver import resolve_setting_value

from .facts import load_runtime_facts
from .roles import REQUIRED_TEAM_ROLES, TEAM_ROLES, RuntimeFacts, auto_assign_roles
from .validation import RUNTIME_TEAM_FRESHNESS_SECONDS, account_validation_state

GLOBAL_TEAM_ROLES: tuple[str, ...] = GLOBAL_TEAM_ROLE_KEYS
DERIVED_TEAM_ROLES: tuple[str, ...] = ("technical_lead", "researcher")
"""Roles sin contrato propio de elegibilidad: usan la elegibilidad del PO y heredan su orden si están vacíos."""
TEAM_ROLE_SOURCES: tuple[str, ...] = ("project", "general", "automatic", "automatic_fallback", "inherited")
"""``automatic_fallback``: el rol tenía lista manual pero ningún id quedó activo y elegible."""
TEAM_ROLE_SETTING_PREFIX = "team.role."


@dataclass(frozen=True)
class RoleAssignment:
    """Orden configurado y efectivo de un rol, con su procedencia y los ids descartados."""

    role: str
    configured: tuple[str, ...]
    effective: tuple[str, ...]
    source: str
    invalid: tuple[str, ...] = ()

    @property
    def assigned(self) -> str | None:
        """Proveedor asignado: el primero del orden efectivo, o ``None`` si el rol no tiene candidatos."""
        return self.effective[0] if self.effective else None


@dataclass(frozen=True)
class GlobalTeam:
    """Equipo global resuelto para un proyecto (o para el scope general con ``project_id=None``)."""

    roles: Mapping[str, RoleAssignment]
    allowed_runtimes: tuple[str, ...]

    def role_order(self, role: str) -> list[str]:
        """Orden efectivo del rol (asignado primero); vacío si el rol no existe o no tiene candidatos."""
        assignment = self.roles.get(role)
        return list(assignment.effective) if assignment else []

    def sealed(self) -> dict[str, Any]:
        """Forma que se sella en la metadata del run (`globalRuntimeTeam`); solo datos, sin objetos."""
        return {
            "roleRuntimeOrder": {role: list(item.effective) for role, item in self.roles.items()},
            "roleRuntimes": {role: item.assigned for role, item in self.roles.items()},
            "source": {role: item.source for role, item in self.roles.items()},
            "allowedRuntimes": list(self.allowed_runtimes),
        }


def _clean_ids(values: Iterable[Any] | None) -> list[str]:
    """Ids sin blancos ni duplicados, en orden; un valor que no es lista (dato corrupto) cuenta como vacío."""
    if isinstance(values, (str, bytes)) or values is None:
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _configured_order(repo: SettingsRepository, role: str, project_id: str | None) -> tuple[list[str], str]:
    """Lista configurada del rol y su scope. Una lista vacía no es un override: se sigue heredando."""
    key = f"{TEAM_ROLE_SETTING_PREFIX}{role}"
    if project_id:
        project_value = repo.get_value(key, "project", project_id)
        if project_value is not UNSET and _clean_ids(project_value):
            return _clean_ids(project_value), "project"
    general_value = repo.get_value(key, "general", None)
    if general_value is not UNSET and _clean_ids(general_value):
        return _clean_ids(general_value), "general"
    return [], "automatic"


def _eligibility_role(role: str) -> str:
    return "product_owner" if role in DERIVED_TEAM_ROLES else role


def _ranked(facts: Sequence[RuntimeFacts], runtime_order: Sequence[str]) -> list[RuntimeFacts]:
    """Mismo ranking que ``auto_assign_roles``: CLI primero, luego ``runtimeOrder``, luego id."""
    order = {provider_id: index for index, provider_id in enumerate(runtime_order)}
    return sorted(
        facts,
        key=lambda item: (item.kind != "cli", order.get(item.provider_id, len(order)), item.provider_id),
    )


def _automatic_ranking(connection: sqlite3.Connection, ranked: Sequence[RuntimeFacts]) -> list[RuntimeFacts]:
    """Orden del reparto automático: suspendidos por cuota al final, luego frescos primero, luego ``ranked``.

    ``sorted`` es estable, así dentro de cada grupo se conserva el ranking de siempre. La frescura y las
    suspensiones se leen de SQLite (evidencia de ejecución y ``provider_limits``/uso), sin red.
    """
    suspended = QuotaManager(connection).providers_in_cooldown()

    def fresh(provider_id: str) -> bool:
        state = account_validation_state(
            connection, provider_id, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
        )
        return state.status == "validated"

    freshness = {item.provider_id: fresh(item.provider_id) for item in ranked}
    return sorted(ranked, key=lambda item: (item.provider_id in suspended, not freshness[item.provider_id]))


def _runtime_order(connection: sqlite3.Connection) -> list[str]:
    try:
        preferences = RuntimeConfigRepository(connection).get_preferences()
    except KeyError:
        return []
    return _clean_ids(preferences.get("runtimeOrder") or [])


def _active_facts(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    facts: Mapping[str, RuntimeFacts] | None,
    offline: bool = False,
) -> dict[str, RuntimeFacts]:
    """Proveedores habilitados, no vetados por la política y dentro de `project.runtime.allowedProviders`."""
    source = (
        facts if facts is not None else load_runtime_facts(connection, project_id=project_id, offline=offline)
    )
    active = {provider_id: item for provider_id, item in source.items() if not item.policy_denied_reason}
    if project_id:
        allowed = set(
            _clean_ids(
                resolve_setting_value(
                    connection=connection, key="project.runtime.allowedProviders", project_id=project_id
                )
            )
        )
        if allowed:
            active = {provider_id: item for provider_id, item in active.items() if provider_id in allowed}
    return active


def _resolve(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    facts: Mapping[str, RuntimeFacts] | None,
    offline: bool = False,
) -> tuple[GlobalTeam, list[RuntimeFacts]]:
    """Resuelve el equipo y devuelve también el orden automático de activos (``describe_global_team``)."""
    active = _active_facts(connection, project_id=project_id, facts=facts, offline=offline)
    runtime_order = _runtime_order(connection)
    ranked = _automatic_ranking(connection, _ranked(list(active.values()), runtime_order))
    automatic = auto_assign_roles(ranked, runtime_order, preranked=True)
    repo = SettingsRepository(connection)
    roles: dict[str, RoleAssignment] = {}

    def automatic_order(role: str) -> tuple[str, ...]:
        if role in DERIVED_TEAM_ROLES:
            # GLOBAL_TEAM_ROLES pone al PO primero; si ese orden cambiara, un rol derivado sin PO resuelto
            # queda vacío en vez de lanzar KeyError.
            product_owner = roles.get("product_owner")
            return product_owner.effective if product_owner else ()
        eligible = [item.provider_id for item in ranked if role in item.eligible_roles]
        first = automatic.get(role)
        return tuple(([first] if first else []) + [pid for pid in eligible if pid != first])

    for role in GLOBAL_TEAM_ROLES:
        eligibility = _eligibility_role(role)
        eligible = {item.provider_id for item in ranked if eligibility in item.eligible_roles}
        configured, source = _configured_order(repo, role, project_id)
        if not configured:
            source = "inherited" if role in DERIVED_TEAM_ROLES else "automatic"
            roles[role] = RoleAssignment(role, (), automatic_order(role), source)
            continue
        # Manual: el orden del operador, sin reordenar por frescura ni descartar lo vencido.
        effective = tuple(provider_id for provider_id in configured if provider_id in eligible)
        invalid = tuple(provider_id for provider_id in configured if provider_id not in eligible)
        if not effective:
            effective, source = automatic_order(role), "automatic_fallback"
        roles[role] = RoleAssignment(role, tuple(configured), effective, source, invalid)
    allowed = _clean_ids(provider_id for item in roles.values() for provider_id in item.effective)
    return GlobalTeam(roles=roles, allowed_runtimes=tuple(allowed)), ranked


def resolve_global_team(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    facts: Mapping[str, RuntimeFacts] | None = None,
    offline: bool = False,
) -> GlobalTeam:
    """Resuelve el equipo global: por rol, manual (project > general) o automático, filtrado a activos.

    ``offline`` usa solo el estado persistido/cacheado de los runtimes (sin sondas de red): el sellado
    corre dentro de la transacción del envío, donde el I/O externo está vetado.
    """
    team, _ranked_active = _resolve(connection, project_id=project_id, facts=facts, offline=offline)
    return team


def describe_global_team(connection: sqlite3.Connection, *, project_id: str | None) -> dict[str, Any]:
    """Cuerpo de ``GET /api/v1/runtime/team``: equipo efectivo por rol más los candidatos activos."""
    from .candidates import RuntimeTeamCandidatesService

    facts = load_runtime_facts(connection, project_id=project_id)
    team, ranked = _resolve(connection, project_id=project_id, facts=facts)
    candidates = RuntimeTeamCandidatesService(connection).list_candidates(
        project_id=project_id, selected=None, facts=facts
    )
    return {
        "roles": [
            {
                "role": role,
                "required": role in REQUIRED_TEAM_ROLES,
                "configured": list(item.configured),
                "effective": list(item.effective),
                "assigned": item.assigned,
                "source": item.source,
                "invalid": list(item.invalid),
                "candidates": [
                    fact.provider_id for fact in ranked if _eligibility_role(role) in fact.eligible_roles
                ],
            }
            for role, item in team.roles.items()
        ],
        "allowedRuntimes": list(team.allowed_runtimes),
        "activeProviders": len(ranked),
        "candidates": candidates["candidates"],
    }


__all__ = [
    "DERIVED_TEAM_ROLES",
    "GLOBAL_TEAM_ROLES",
    "TEAM_ROLES",
    "TEAM_ROLE_SETTING_PREFIX",
    "TEAM_ROLE_SOURCES",
    "GlobalTeam",
    "RoleAssignment",
    "describe_global_team",
    "resolve_global_team",
]
