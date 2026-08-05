"""Bootstrap determinista del equipo base de agentes sobre perfiles operacionales.

El roster base vive en ``agent_profiles`` porque esos perfiles ya son el contrato usado por
runs, permisos, skills, limites de costo y UI. La fuente única de defaults es el scheduler;
el bootstrap crea solamente los roles activos que falten y no sobrescribe perfiles existentes.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.team_scheduler.scheduler import ALL_ROLES, team_member_defaults

from .repository import AgentsRepository
from .routing_profiles import RoutingProfileStore

BASE_TEAM_PROFILES: tuple[dict[str, Any], ...] = tuple(team_member_defaults())
_PROFILE_BY_ROLE: dict[str, dict[str, Any]] = {
    str(profile["role"]): profile for profile in BASE_TEAM_PROFILES
}


def bootstrap_base_team_if_needed(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Crea los perfiles base que falten, identificándolos por id y no por rol.

    El guard va por id a propósito: un runtime singleton (``product_owner_agent`` sobre el rol
    ``product_owner``, ``git_workspace_agent`` sobre ``devops_engineer``) ocupa el rol y, con un
    guard por rol, suprimía para siempre la creación del perfil base correspondiente. El operador
    veía un roster incompleto sin explicación. Sigue sin sobrescribirse ningún perfil existente.
    """
    existing_ids = {str(row["id"]) for row in connection.execute("SELECT id FROM agent_profiles").fetchall()}
    repository = AgentsRepository(connection)
    created: list[dict[str, Any]] = []
    for profile in BASE_TEAM_PROFILES:
        if profile["id"] in existing_ids:
            continue
        created.append(repository.upsert_agent_profile(profile))
    bootstrap_role_model_policies_if_needed(connection)
    return created


def bootstrap_role_model_policies_if_needed(connection: sqlite3.Connection) -> list[str]:
    """Crea la política de modelo que falte para cada rol del scheduler, sin pisar las existentes.

    Un rol sin política caía al fallback silencioso a ``developer``, que enruta el trabajo a un
    perfil que no es el suyo sin avisar. Sembrar los 17 roles hace explícito el enrutamiento.

    ``preferred`` se deja vacío a propósito: el contrato exige pares ``{provider, model}`` y en el
    arranque todavía no se sabe qué modelos expone cada endpoint, así que sembrar preferencias
    inventadas produciría candidatos inexistentes. Vacío significa "sin pin", y el orden lo resuelve
    la selección de recursos; lo que importa aquí es que la fila exista con sus límites de costo.

    El criterio completo (por qué 12 roles quedan sin pin, qué se pierde y cómo pinear uno desde la
    UI) está en ``docs/model-routing.md`` §"Role pins".

    Returns:
        Los roles para los que se creó una política nueva.
    """
    store = RoutingProfileStore(connection)
    existing_roles = {str(policy["role"]) for policy in store.list_role_policies()}
    seeded: list[str] = []
    for role in ALL_ROLES:
        if role in existing_roles:
            continue
        profile = _PROFILE_BY_ROLE.get(role)
        if profile is None:
            continue
        store.upsert_role_policy(
            {
                "role": role,
                "maxCostPerTaskUsd": profile.get("maxCostPerRun", 0),
                "maxTokensPerRun": profile.get("maxTokensPerRun", 0),
                "requiresApprovalOverUsd": profile.get("requiresApprovalOverUsd"),
            }
        )
        seeded.append(role)
    return seeded
