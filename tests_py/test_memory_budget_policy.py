"""Tests del piso de memoria, gemelo exacto del de disco.

Medido: con 21,9 GiB disponibles y el default `resources.minFreeMemoryGiB = 16`, el presupuesto
queda en 5,9 GiB. El perfil `agent_cli` pide 8, asi que **ningun trabajo de agente puede admitirse
jamas** en este equipo. El sintoma observado: `test_watchdog_http_pipeline` fallaba 7 de 7 por
deadline, porque el worker recibia el job y lo diferia para siempre.

Peor en equipos chicos: en uno de 16 GiB el piso absoluto de 16 es el 100% de la RAM.

Se aplica la misma forma que al disco —el menor entre absoluto y porcentaje— con una diferencia que
importa: **nunca puede bajar del piso duro**. Quedarse sin RAM es mas inmediato que quedarse sin
disco, y `hardFreeMemoryGiB` existe justamente para eso.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.profiles import effective_min_free_memory_bytes
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

GIB = 1024**3


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
        yield handle


def _floor(connection, *, total_gib: float) -> float:
    snapshot = ResourceSnapshot.test_snapshot(total_memory_bytes=int(total_gib * GIB))
    return effective_min_free_memory_bytes(connection, snapshot=snapshot) / GIB


def test_this_machine_can_finally_admit_an_agent(connection) -> None:
    """El caso medido: 64 GiB de RAM, 21,9 disponibles y un `agent_cli` que pide 8."""
    piso = _floor(connection, total_gib=64)
    presupuesto = 21.9 - piso

    assert presupuesto >= 8, f"con un piso de {piso:.1f} GiB el presupuesto es {presupuesto:.1f}"


def test_the_hard_floor_is_never_crossed(connection) -> None:
    """Quedarse sin RAM es inmediato: el piso duro manda sobre cualquier calculo relativo."""
    with connection:
        SettingsRepository(connection).set_value("resources.hardFreeMemoryGiB", "general", None, 8)

    assert _floor(connection, total_gib=16) >= 8


def test_a_large_machine_keeps_the_absolute_floor(connection) -> None:
    """256 GiB: el 10% serian 25,6 GiB, mas de lo necesario. Manda el absoluto."""
    assert _floor(connection, total_gib=256) == pytest.approx(16, rel=0.01)


def test_the_operator_can_still_demand_more(connection) -> None:
    """Bajar el default no le quita al operador la posibilidad de ser mas estricto."""
    with connection:
        SettingsRepository(connection).set_value("resources.minFreeMemoryGiB", "general", None, 40)

    assert _floor(connection, total_gib=256) == pytest.approx(25.6, rel=0.01)
