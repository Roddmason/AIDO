"""Shared Product Loop metadata helpers.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.settings.resolver import resolve_setting_value
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


def seal_operator_cost_decision(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Sella la decisión de costo vigente del operador sobre la metadata del run.

    ``project.loop.teamMode`` (economy/balanced/critical/maximum) es un *default*: una metadata
    con un modo válido lo respeta, porque elegir el modo por ejecución es legítimo.
    ``project.routing.forceLocal`` NO es un default sino un control de privacidad: cuando está
    activo fuerza ``privacyLevel=local_private`` por encima de cualquier metadata, de modo que
    un campo por mensaje jamás pueda relajar una restricción que el operador dejó puesta.
    Sellar aquí deja la decisión en el payload del job, auditable junto al run que la usó.
    """
    stamped = dict(metadata)
    requested_mode = str(stamped.get("teamMode") or stamped.get("team_mode") or "").strip().lower()
    if requested_mode not in MODES:
        resolved_mode = resolve_setting_value(
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
    return stamped
