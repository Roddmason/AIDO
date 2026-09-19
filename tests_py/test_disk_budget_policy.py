"""Tests del piso de disco: un umbral que el equipo no puede alcanzar no protege, solo bloquea.

Medido en esta máquina: el default `resources.minFreeDiskGiB = 50` contra 42-48 GiB libres en C:.
El gobernador rechazaba **todo** spawn con `minimum_free_disk`, y como los tests arrancan con una
base nueva —sin los overrides del operador— heredaban ese default y caían en bloque: 21 rojos en una
corrida, 86 rechazos, ninguno por el código.

Es el mismo patrón que el TTL de salud: un contrato inalcanzable no da seguridad, solo apaga el
producto. La forma correcta la tiene Kubernetes, cuyas señales de eviction aceptan **porcentaje o
valor absoluto** (`nodefs.available: "1Gi"`), porque un piso absoluto no significa lo mismo en un
disco de 256 GB que en uno de 2 TB.

Acá el piso efectivo es el **menor** de los dos: el absoluto deja de volverse inalcanzable en discos
chicos, y el porcentaje conserva protección proporcional en los grandes.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.profiles import effective_min_free_disk_bytes
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
    snapshot = ResourceSnapshot.test_snapshot(
        disk_free_bytes={"C:": int(total_gib * 0.2 * GIB)},
        disk_total_bytes={"C:": int(total_gib * GIB)},
    )
    return effective_min_free_disk_bytes(connection, snapshot=snapshot) / GIB


def test_on_a_small_disk_the_absolute_floor_stops_being_unreachable(connection) -> None:
    """256 GB: exigir 50 GiB libres es exigir el 20% del disco y bloquea el equipo entero."""
    assert _floor(connection, total_gib=256) == pytest.approx(256 * 0.05, rel=0.01)


def test_on_a_large_disk_the_absolute_floor_still_protects(connection) -> None:
    """2 TB: el 5% serían 100 GiB, mucho más de lo necesario. Manda el absoluto."""
    assert _floor(connection, total_gib=2048) == pytest.approx(50, rel=0.01)


def test_this_machine_can_actually_satisfy_the_default(connection) -> None:
    """El caso que motivó todo: 465 GB con ~45 GiB libres tiene que poder trabajar."""
    piso = _floor(connection, total_gib=465)

    assert piso < 42, f"el piso por defecto ({piso:.1f} GiB) sigue por encima de lo que este equipo tiene"


def test_the_operator_can_still_demand_more_than_the_default(connection) -> None:
    """Un piso escrito a mano se respeta tal cual: el porcentaje sólo rescata el default.

    Esta es la regresión que la suite completa encontró. La primera versión tomaba el menor
    **siempre**, así que el operador que pedía 120 GiB recibía 102,4 y nadie se lo decía. El
    porcentaje existe para corregir un número que no puede conocer la máquina, no a una persona.
    """
    with connection:
        SettingsRepository(connection).set_value("resources.minFreeDiskGiB", "general", None, 120)

    assert _floor(connection, total_gib=2048) == pytest.approx(120, rel=0.01)


def test_an_operator_floor_is_never_lowered_by_the_percentage(connection) -> None:
    """El caso exacto que rompió 10 tests: 120 GiB pedidos en un disco donde el 5% son 12,8."""
    with connection:
        SettingsRepository(connection).set_value("resources.minFreeDiskGiB", "general", None, 120)

    assert _floor(connection, total_gib=256) == pytest.approx(120, rel=0.01)


def test_without_capacity_information_it_falls_back_to_the_absolute_floor(connection) -> None:
    """Un snapshot viejo o parcial no puede relajar el piso por accidente."""
    sin_total = ResourceSnapshot.test_snapshot(disk_free_bytes={"C:": 100 * GIB}, disk_total_bytes={})

    assert effective_min_free_disk_bytes(connection, snapshot=sin_total) == 50 * GIB


def test_the_smallest_volume_decides(connection) -> None:
    """El gobernador mira el volumen más apretado; el piso tiene que calcularse sobre ese mismo."""
    mixto = ResourceSnapshot.test_snapshot(
        disk_free_bytes={"C:": 40 * GIB, "H:": 400 * GIB},
        disk_total_bytes={"C:": 256 * GIB, "H:": 4096 * GIB},
    )

    assert effective_min_free_disk_bytes(connection, snapshot=mixto) == pytest.approx(
        256 * 0.05 * GIB, rel=0.01
    )
