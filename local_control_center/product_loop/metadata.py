"""Shared Product Loop metadata helpers.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.runtime_team.configuration import (
    THREAD_RUN_CONFIGURATION_KEY,
    seal_thread_runtime_team,
)
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now
from local_control_center.team_scheduler.scheduler import MODES

RESOURCE_COST_POLICY_METADATA_KEYS = frozenset(
    {
        "allowUnknownCost",
        "allow_unknown_cost",
        "requireApprovalForUnknownCost",
        "require_approval_for_unknown_cost",
    }
)


def strip_untrusted_resource_cost_policy_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Remove caller-supplied cost policy overrides from Product Loop metadata."""
    clean = dict(metadata or {})
    for key in RESOURCE_COST_POLICY_METADATA_KEYS:
        clean.pop(key, None)
    return clean


def _remembered_team_mode(connection: sqlite3.Connection, thread_id: str | None) -> str | None:
    """Ultimo modo valido que el operador eligio en ese hilo, o ``None`` si no hay ninguno."""
    if not thread_id:
        return None
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread_id,)).fetchone()
    if row is None:
        return None
    remembered = json_loads(row["metadata"], {}).get(THREAD_RUN_CONFIGURATION_KEY) or {}
    mode = str(remembered.get("teamMode") or "").strip().lower()
    return mode if mode in MODES else None


def _remember_team_mode(connection: sqlite3.Connection, thread_id: str, mode: str) -> None:
    """Deja el modo elegido en la metadata del hilo para los mensajes que vengan despues."""
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread_id,)).fetchone()
    if row is None:
        return
    metadata = json_loads(row["metadata"], {})
    configuration = dict(metadata.get(THREAD_RUN_CONFIGURATION_KEY) or {})
    if configuration.get("teamMode") == mode:
        return
    configuration["teamMode"] = mode
    metadata[THREAD_RUN_CONFIGURATION_KEY] = configuration
    connection.execute(
        "UPDATE project_threads SET metadata = ?, updated_at = ? WHERE id = ?",
        (json_dumps(metadata), utc_now(), thread_id),
    )


def seal_operator_cost_decision(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    metadata: dict[str, Any],
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Sella la decisión de costo vigente del operador sobre la metadata del run.

    ``project.loop.teamMode`` (economy/balanced/critical/maximum) es un *default*: una metadata
    con un modo válido lo respeta, porque elegir el modo por ejecución es legítimo.
    El hilo se mete en el medio de esa cadena: ``mensaje > hilo > proyecto > general > default``.
    Lo que el operador elige explicitamente queda recordado en el hilo, asi que no tiene que
    repetirlo; un hilo nuevo sigue naciendo con el default del proyecto. El equipo de runtimes del
    hilo se sella como ``runtimeTeam`` y reemplaza cualquier valor entrante.

    ``project.routing.forceLocal`` NO es un default sino un control de privacidad: cuando está
    activo fuerza ``privacyLevel=local_private`` por encima de cualquier metadata, de modo que
    un campo por mensaje jamás pueda relajar una restricción que el operador dejó puesta.
    Sellar aquí deja la decisión en el payload del job, auditable junto al run que la usó.
    """
    stamped = dict(metadata)
    requested_mode = str(stamped.get("teamMode") or stamped.get("team_mode") or "").strip().lower()
    if requested_mode in MODES:
        # El operador eligio explicitamente: el hilo lo recuerda para los mensajes siguientes.
        # Trabajar en un hilo es trabajar en un contexto; repetir su configuracion en cada
        # mensaje es friccion pura.
        if thread_id:
            _remember_team_mode(connection, thread_id, requested_mode)
    else:
        resolved_mode = _remembered_team_mode(connection, thread_id) or resolve_setting_value(
            connection=connection,
            key="project.loop.teamMode",
            project_id=project_id,
        )
        if resolved_mode in MODES:
            stamped["teamMode"] = resolved_mode
    if resolve_setting_value(
        connection=connection,
        key="project.routing.forceLocal",
        project_id=project_id,
    ):
        stamped["privacyLevel"] = "local_private"
    return seal_thread_runtime_team(connection, project_id=project_id, thread_id=thread_id, metadata=stamped)
