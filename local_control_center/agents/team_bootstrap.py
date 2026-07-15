"""Bootstrap determinista del equipo base de agentes sobre perfiles operacionales.

El roster base vive en ``agent_profiles`` porque esos perfiles ya son el contrato usado por
runs, permisos, skills, limites de costo y UI. La fuente única de defaults es el scheduler;
el bootstrap crea solamente los roles activos que falten y no sobrescribe perfiles existentes.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.team_scheduler.scheduler import team_member_defaults

from .repository import AgentsRepository

BASE_TEAM_PROFILES: tuple[dict[str, Any], ...] = tuple(team_member_defaults())


def bootstrap_base_team_if_needed(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Create missing active base roles without overwriting existing active profiles for the same role."""
    active_roles = {
        str(row["role"])
        for row in connection.execute("SELECT role FROM agent_profiles WHERE status = 'active'").fetchall()
    }
    repository = AgentsRepository(connection)
    created: list[dict[str, Any]] = []
    for profile in BASE_TEAM_PROFILES:
        if profile["role"] in active_roles:
            continue
        created.append(repository.upsert_agent_profile(profile))
    return created
