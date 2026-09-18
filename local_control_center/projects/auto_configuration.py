"""Configuracion que el proyecto se asigna solo al nacer, deducida de sus propios manifiestos.

Un proyecto nuevo nacia sin puerta de calidad: `project.quality.gateCommands` es `[]` por defecto,
asi que el operador tenia que escribir a mano comandos que ya estaban escritos en el `pom.xml`, el
`package.json` o el `Cargo.toml` del repo. La deteccion existia (`projects/discovery.py`) pero su
resultado era efimero, y lo poco que llegaba al alta venia del **cliente** — dato no confiable —
en vez de calcularlo el servidor.

El invariante de este modulo no es "auto-asignar": es **no pisar nunca al operador**. Un valor que
la persona puso gana siempre, incluso frente a una deteccion mejor, y se distingue de uno que puso
la maquina por quien lo asigno (`assigned_by`). Volver a detectar solo puede corregir lo que la maquina
misma habia supuesto.

Es de solo lectura sobre el proyecto: lee manifiestos, nunca ejecuta nada.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.settings.registry import descriptor_for
from local_control_center.settings.repository import UNSET, SettingsRepository

from .toolchain import plan_workspace_commands

ASSIGNED_BY_DETECTION = "auto"
"""Quien asigno el valor cuando lo puso la deteccion y no una persona."""

GATE_COMMANDS_KEY = "project.quality.gateCommands"


def derive_project_settings(project_path: str | Path) -> dict[str, Any]:
    """Valores de configuracion que se deducen de los manifiestos del proyecto.

    Devuelve un dict vacio cuando no se reconoce ninguna toolchain: inventarle una puerta de
    calidad a un repo que no se entiende es peor que no ponerle ninguna, porque despues el operador
    cree que hay verificacion donde no la hay.
    """
    commands = [command.policy_command for command in plan_workspace_commands(project_path)]
    if not commands:
        return {}
    return {GATE_COMMANDS_KEY: commands}


def apply_project_auto_configuration(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    project_path: str | Path,
) -> dict[str, Any]:
    """Escribe la configuracion deducida sin tocar lo que el operador haya definido.

    Returns:
        ``assigned`` con lo que se escribio, ``preserved`` con las claves que ya tenian un valor
        del operador y por eso no se tocaron, y ``detected`` con todo lo que la deteccion propuso.
    """
    derived = derive_project_settings(project_path)
    repository = SettingsRepository(connection)
    assigned: dict[str, Any] = {}
    preserved: list[str] = []
    for key, value in derived.items():
        if descriptor_for(key) is None:  # pragma: no cover - el registry es la fuente de verdad
            continue
        stored = repository.get_value(key, "project", project_id)
        if (
            stored is not UNSET
            and repository.get_assigned_by(key, "project", project_id) != ASSIGNED_BY_DETECTION
        ):
            preserved.append(key)
            continue
        repository.set_value(key, "project", project_id, value, assigned_by=ASSIGNED_BY_DETECTION)
        assigned[key] = value
    return {"assigned": assigned, "preserved": preserved, "detected": derived}
