"""Test del presupuesto de disco que la propia suite consume mientras corre.

La suite completa fallaba de forma masiva y variable — 0, 2, 9 y 21 rojos en corridas del mismo
commit — y las 21 trazaban a **una sola causa**: `resource_wait: minimum_free_disk`, 86 veces. El
gobernador rechaza la admision de cualquier subproceso cuando un volumen baja del umbral, asi que
todo test que lance un proceso cae junto.

El mecanismo medido: pytest retiene el scratch de **todos** los tests, no solo de los que fallan.
Se encontraron 13 directorios retenidos con 5,35 GiB, uno solo de 3,51 GiB, y 14,2 GiB creados en
TEMP en un dia. Con 48 GiB libres y un umbral de 30, una corrida larga se come su propio margen y a
partir de ahi falla por disco, no por codigo.

El scratch de un test que **paso** no tiene valor diagnostico. El de uno que fallo, si.

@author Rodrigo Mason
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pytest_options() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def test_only_failed_tests_keep_their_scratch() -> None:
    """Retener el scratch de lo que paso es lo que hace que la suite se quede sin disco sola."""
    assert _pytest_options().get("tmp_path_retention_policy") == "failed"


def test_the_number_of_retained_runs_is_bounded() -> None:
    """Sin techo, cada corrida suma: se midieron 13 directorios acumulados."""
    retained = _pytest_options().get("tmp_path_retention_count")

    assert isinstance(retained, int) and 1 <= retained <= 3, retained
