"""Tests de la retención de las dos fuentes que más hacen crecer la base sin aportar nada.

Medido en la instalación real (2026-09-18, base de 756,4 MB):

- `resource_admission_decisions`: 130.770 filas / 119,3 MB. **Cero poda.** Cada intento de admisión
  guarda su `request_json` y su `snapshot_json` completos, para siempre.
- `events` de alto volumen: `worker_idle` 274.091 filas / 17,0 MB y `job.resource_wait` 128.400
  filas / 21,4 MB. Sólo `telemetry.http.request` tenía poda; los otros dos no.

Son ~158 MB de 756: el **21% de la base**. `worker_idle` en particular son 274 mil filas cuyo
contenido es que no pasó nada.

Lo que estos tests protegen no es "borrar": es **qué NO se borra**. La auditoría, las decisiones y
los eventos de dominio no se tocan nunca, por antiguos que sean.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.host_resources.retention import (
    ADMISSION_DECISION_RETENTION_SECONDS,
    prune_resource_history,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.telemetry import (
    HIGH_VOLUME_EVENT_TYPES,
    prune_high_volume_events,
)


def _iso(days_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
        yield handle


def _event(connection, event_type: str, days_ago: float, identifier: str) -> None:
    connection.execute(
        "INSERT INTO events (id, job_id, project_id, type, payload, created_at) "
        "VALUES (?, NULL, NULL, ?, '{}', ?)",
        (identifier, event_type, _iso(days_ago)),
    )


def test_the_noisiest_event_types_are_pruned(connection) -> None:
    """`worker_idle` son 274 mil filas que dicen que no pasó nada; no pueden quedarse para siempre."""
    with connection:
        _event(connection, "worker_idle", 30, "viejo-1")
        _event(connection, "job.resource_wait", 30, "viejo-2")
        _event(connection, "telemetry.http.request", 30, "viejo-3")

        removed = prune_high_volume_events(connection)

    assert removed == 3
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_domain_events_survive_no_matter_how_old(connection) -> None:
    """El invariante que importa: sólo se poda ruido de alto volumen, jamás historia del producto."""
    with connection:
        for index, event_type in enumerate(
            ("thread.created", "agent.devops.passed", "project.create", "worker_failed")
        ):
            _event(connection, event_type, 400, f"dominio-{index}")

        removed = prune_high_volume_events(connection)

    assert removed == 0
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 4


def test_recent_noise_is_kept_because_it_still_diagnoses(connection) -> None:
    """Lo reciente sí sirve: es lo que se mira cuando algo acaba de fallar."""
    with connection:
        _event(connection, "worker_idle", 1, "reciente")
        _event(connection, "worker_idle", 30, "viejo")

        removed = prune_high_volume_events(connection)

    assert removed == 1
    survivors = [row[0] for row in connection.execute("SELECT id FROM events")]
    assert survivors == ["reciente"]


def test_every_pruned_type_is_declared_on_purpose() -> None:
    """La lista es de inclusión: un tipo nuevo nace conservado, nunca borrado por descuido."""
    assert (
        frozenset({"telemetry.http.request", "worker_idle", "job.resource_wait"}) == HIGH_VOLUME_EVENT_TYPES
    )


def test_admission_decisions_are_pruned_with_the_rest_of_the_resource_history(connection) -> None:
    """119,3 MB en una tabla sin poda: el mecanismo existía y sólo se había cableado a las muestras."""
    repository = ResourceRepository(connection)
    with connection:
        for index, days in enumerate((30, 30, 0.5)):
            connection.execute(
                "INSERT INTO resource_admission_decisions "
                "(id, execution_id, job_id, workload_class, owner_id, status, reason_code, reason, "
                " request_json, snapshot_json, lease_id, created_at) "
                "VALUES (?, ?, NULL, 'qa_light', 'w', 'resource_wait', 'host_cpu_saturated', '', "
                " '{}', '{}', NULL, ?)",
                (f"decision-{index}", f"exec-{index}", _iso(days)),
            )

        removed = repository.prune_admission_decisions(retention_seconds=ADMISSION_DECISION_RETENTION_SECONDS)

    assert removed == 2
    remaining = connection.execute("SELECT id FROM resource_admission_decisions").fetchall()
    assert [row[0] for row in remaining] == ["decision-2"]


def test_pruning_the_resource_history_covers_both_tables(connection) -> None:
    """Una sola llamada tiene que cubrir muestras Y decisiones, o la segunda se vuelve a olvidar."""
    repository = ResourceRepository(connection)
    with connection:
        connection.execute(
            "INSERT INTO resource_admission_decisions "
            "(id, execution_id, job_id, workload_class, owner_id, status, reason_code, reason, "
            " request_json, snapshot_json, lease_id, created_at) "
            "VALUES ('vieja', 'exec', NULL, 'qa_light', 'w', 'admitted', '', '', '{}', '{}', NULL, ?)",
            (_iso(30),),
        )

        prune_resource_history(repository)

    assert connection.execute("SELECT COUNT(*) FROM resource_admission_decisions").fetchone()[0] == 0
