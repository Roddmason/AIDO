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
    drain_resource_history,
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


def _admit(repository, connection, *, execution: str, job: str, reason: str, status: str = "resource_wait"):
    """Registra una decisión de admisión como lo hace el gobernador."""
    from local_control_center.host_resources.models import (
        ResourceAdmissionDecision,
        ResourceAdmissionRequest,
        ResourceSnapshot,
    )

    with connection:
        repository.record_admission(
            request=ResourceAdmissionRequest(
                execution_id=execution, workload_class="qa_light", owner_id="w", job_id=job
            ),
            decision=ResourceAdmissionDecision(
                status=status,
                reason_code=reason,
                reason="",
                lease=None,
                snapshot=ResourceSnapshot.test_snapshot(),
            ),
        )


def test_repeating_the_same_verdict_does_not_grow_the_table(connection) -> None:
    """98% de esa tabla eran rechazos identicos repetidos, y ningun lector los consume.

    Medido en la instalación real: 130.781 filas para 2.986 ejecuciones — 44 intentos promedio, y
    un job con **9.023 intentos en 58 minutos**. Los dos lectores que existen piden sólo la fila más
    reciente (`ORDER BY rowid DESC LIMIT 1` y `MAX(rowid)`), así que el intento 9.023 no le servía a
    nadie y se escribía con el snapshot completo del host, bajo el candado global, justo cuando el
    equipo ya estaba en apuros.
    """
    repository = ResourceRepository(connection)
    for _ in range(50):
        _admit(repository, connection, execution="exec-1", job="job-1", reason="host_cpu_saturated")

    rows = connection.execute(
        "SELECT attempts FROM resource_admission_decisions WHERE execution_id='exec-1'"
    ).fetchall()

    assert len(rows) == 1, f"50 veredictos identicos dejaron {len(rows)} filas"
    assert rows[0]["attempts"] == 50


def test_a_different_verdict_is_recorded_as_its_own_row(connection) -> None:
    """Cambiar de motivo SÍ es información nueva: la cronología del bloqueo no se pierde."""
    repository = ResourceRepository(connection)
    _admit(repository, connection, execution="exec-1", job="job-1", reason="host_cpu_saturated")
    _admit(repository, connection, execution="exec-1", job="job-1", reason="minimum_free_memory")
    _admit(repository, connection, execution="exec-1", job="job-1", reason="host_cpu_saturated")

    reasons = [
        row["reason_code"]
        for row in connection.execute(
            "SELECT reason_code FROM resource_admission_decisions WHERE execution_id='exec-1' ORDER BY rowid"
        )
    ]

    assert reasons == ["host_cpu_saturated", "minimum_free_memory", "host_cpu_saturated"]


def test_the_wait_keeps_the_time_it_started_not_the_last_retry(connection) -> None:
    """`waiting_requests` ordena por `created_at ASC` para atender primero al que mas espera.

    Con una fila nueva por reintento, ese campo era la hora del ÚLTIMO intento, así que el que más
    esperaba se iba al final justo por seguir esperando. Conservarlo hace que el orden signifique
    lo que dice.
    """
    repository = ResourceRepository(connection)
    _admit(repository, connection, execution="exec-1", job="job-1", reason="host_cpu_saturated")
    primero = connection.execute(
        "SELECT created_at FROM resource_admission_decisions WHERE execution_id='exec-1'"
    ).fetchone()["created_at"]

    for _ in range(5):
        _admit(repository, connection, execution="exec-1", job="job-1", reason="host_cpu_saturated")

    row = connection.execute(
        "SELECT created_at, last_seen_at FROM resource_admission_decisions WHERE execution_id='exec-1'"
    ).fetchone()

    assert row["created_at"] == primero, "la espera no puede reiniciar su reloj por reintentar"
    assert row["last_seen_at"] >= primero


def test_the_only_two_readers_still_see_the_latest_verdict(connection) -> None:
    """El cambio no puede romper a quien consulta: ambos lectores piden la fila mas reciente."""
    repository = ResourceRepository(connection)
    with connection:
        connection.execute(
            "INSERT INTO projects (id, name, path, template_id, source, status, metadata, "
            "created_at, updated_at) VALUES ('p-1', 'demo', '/tmp/demo', 'other', 'api', "
            "'active', '{}', ?, ?)",
            (_iso(0), _iso(0)),
        )
        connection.execute(
            "INSERT INTO jobs (id, project_id, kind, status, payload, created_at, updated_at) "
            "VALUES ('job-1', 'p-1', 'operation.execute', 'resource_wait', '{}', ?, ?)",
            (_iso(0), _iso(0)),
        )
    for _ in range(20):
        _admit(repository, connection, execution="exec-1", job="job-1", reason="minimum_free_disk")

    waiting = repository.waiting_requests()

    assert [request.execution_id for request in waiting] == ["exec-1"]


def _old_decisions(connection, count: int, *, days_ago: float = 30) -> None:
    with connection:
        connection.executemany(
            "INSERT INTO resource_admission_decisions "
            "(id, execution_id, job_id, workload_class, owner_id, status, reason_code, reason, "
            " request_json, snapshot_json, lease_id, created_at) "
            "VALUES (?, ?, NULL, 'qa_light', 'w', 'resource_wait', 'minimum_free_memory', '', '{}', '{}', NULL, ?)",
            [(f"old-{days_ago}-{index}", f"exec-{index}", _iso(days_ago)) for index in range(count)],
        )


def test_the_backlog_drains_in_bounded_batches_so_the_live_system_keeps_writing(connection) -> None:
    """131.757 decisiones vencidas en la instalación real: borrarlas de una vez retiene el candado.

    Cada lote es su propia transacción corta (la conexión es autocommit), así que el API y el
    worker siguen escribiendo entre lotes. Con un tope de lotes, una corrida nunca se alarga.
    """
    _old_decisions(connection, 12)
    _old_decisions(connection, 2, days_ago=0.5)

    assert drain_resource_history(connection, batch_size=5, max_batches=1) == 5
    assert drain_resource_history(connection, batch_size=5) == 7
    assert connection.execute("SELECT COUNT(*) FROM resource_admission_decisions").fetchone()[0] == 2


def test_the_worker_hourly_prune_actually_drains_the_resource_history(tmp_path: Path) -> None:
    """La guarda que faltaba: probar la función no probaba que producción la llamara.

    `prune_resource_history` cubría las dos tablas desde el 19-09, pero el único llamador de
    producción (`governor.record_sample`) seguía podando solo las muestras.
    """
    from local_control_center.workers.runtime import LocalWorkerRuntime

    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as handle:
        with handle:
            initialize_platform_schema(handle)
        _old_decisions(handle, 3)

    worker = object.__new__(LocalWorkerRuntime)
    worker.db_path = db_path
    worker._last_telemetry_prune_monotonic = None
    worker._prune_telemetry_if_due()

    with closing(open_sqlite_connection(db_path)) as handle:
        assert handle.execute("SELECT COUNT(*) FROM resource_admission_decisions").fetchone()[0] == 0


def test_resolved_evictions_are_kept_a_month_and_active_ones_never_pruned(connection) -> None:
    """Un desalojo cuya lease ya se liberó es evidencia; uno sin resolver sigue cancelando su ejecución."""
    repository = ResourceRepository(connection)
    now = datetime.now(UTC)

    def violation(violation_id: str, *, resolved_days_ago: float | None) -> None:
        resolved_at = (
            None
            if resolved_days_ago is None
            else (now - timedelta(days=resolved_days_ago))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        connection.execute(
            """INSERT INTO resource_violations
               (id, execution_id, lease_id, violation_type, action, reason, created_at, resolved_at)
               VALUES (?, ?, ?, 'hard_memory_floor', 'cancel_non_essential_workload', 'r', ?, ?)""",
            (
                violation_id,
                f"execution-{violation_id}",
                f"lease-{violation_id}",
                resolved_at or now.isoformat(),
                resolved_at,
            ),
        )

    violation("old-resolved", resolved_days_ago=31)
    violation("recent-resolved", resolved_days_ago=2)
    violation("active", resolved_days_ago=None)

    drain_resource_history(connection)

    remaining = {row[0] for row in connection.execute("SELECT id FROM resource_violations")}
    assert remaining == {"recent-resolved", "active"}
    assert repository.prune_resolved_violations(retention_seconds=30 * 24 * 60 * 60) == 0
