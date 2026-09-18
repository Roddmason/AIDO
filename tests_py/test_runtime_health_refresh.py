"""Tests del refresco automático de evidencia de salud: la evidencia caduca a los 300 s y el único
productor es una operación encolada, así que sin refresco automático un sistema bien configurado se
reporta bloqueado a los pocos minutos y después de cada reinicio del control plane.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from local_control_center.agents.runtime_health_refresh import (
    CLI_HEALTH_OPERATION,
    PROVIDER_HEALTH_OPERATION,
    enqueue_stale_health_checks,
    needs_refresh,
    stale_health_targets,
)
from local_control_center.agents.runtime_readiness import HEALTH_EVIDENCE_TTL_SECONDS
from local_control_center.executions.repository import ExecutionRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _iso(seconds_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _status(**overrides) -> dict:
    base = {
        "id": "codex_cli",
        "kind": "cli",
        "configured": True,
        "healthStatus": "healthy",
        "healthCheckedAt": _iso(10),
    }
    base.update(overrides)
    return base


def test_fresh_evidence_is_not_refreshed() -> None:
    """Evidencia recién tomada no gasta una ejecución."""
    assert needs_refresh(_status(healthCheckedAt=_iso(5))) is False


def test_evidence_is_refreshed_before_it_expires() -> None:
    """El refresco se adelanta al vencimiento: evidencia que no llega al próximo ciclo se renueva."""
    almost_expired = HEALTH_EVIDENCE_TTL_SECONDS - 10
    assert needs_refresh(_status(healthCheckedAt=_iso(almost_expired))) is True


def test_expired_and_missing_evidence_are_refreshed() -> None:
    """Sin fecha o con fecha vencida siempre se refresca."""
    assert needs_refresh(_status(healthCheckedAt=None)) is True
    assert needs_refresh(_status(healthCheckedAt="")) is True
    assert needs_refresh(_status(healthCheckedAt=_iso(HEALTH_EVIDENCE_TTL_SECONDS * 3))) is True


def test_unparseable_evidence_is_refreshed_not_trusted() -> None:
    """Una marca ilegible no prueba salud: se refresca en vez de asumirla vigente."""
    assert needs_refresh(_status(healthCheckedAt="no-es-una-fecha")) is True


def test_unconfigured_and_manual_runtimes_are_never_targets() -> None:
    """Refrescar lo no configurado gastaría ejecuciones y llamadas de red inútiles."""
    targets = stale_health_targets(
        [
            _status(id="sin_config", configured=False, healthCheckedAt=None),
            _status(id="manual", kind="manual", healthCheckedAt=None),
            _status(id="codex_cli", healthCheckedAt=None),
        ]
    )
    assert [target[2] for target in targets] == ["codex_cli"]


def test_targets_map_each_kind_to_its_own_operation() -> None:
    """Una CLI y un proveedor no se verifican con la misma operación."""
    targets = stale_health_targets(
        [
            _status(id="codex_cli", kind="cli", healthCheckedAt=None),
            _status(id="gemini", kind="api", healthCheckedAt=None),
            _status(id="ollama", kind="local", healthCheckedAt=None),
        ]
    )
    by_runtime = {runtime: (operation, argument) for operation, argument, runtime in targets}
    assert by_runtime["codex_cli"] == (CLI_HEALTH_OPERATION, "runtime_id")
    assert by_runtime["gemini"] == (PROVIDER_HEALTH_OPERATION, "provider_id")
    assert by_runtime["ollama"] == (PROVIDER_HEALTH_OPERATION, "provider_id")


def _platform(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    create_app(runtime=runtime, static_dir=None)
    return runtime


def test_enqueue_creates_one_execution_per_stale_runtime(tmp_path: Path) -> None:
    """Cada runtime configurado sin evidencia vigente deja su ejecución encolada para el worker."""
    runtime = _platform(tmp_path)
    try:
        result = enqueue_stale_health_checks(
            runtime,
            statuses=[
                _status(id="codex_cli", kind="cli", healthCheckedAt=None),
                _status(id="gemini", kind="api", healthCheckedAt=None),
            ],
        )

        assert result["enqueued"] == 2, result
        operations = {
            execution["operation"] for execution in ExecutionRepository(runtime.connection).list_recent()
        }
        assert operations == {CLI_HEALTH_OPERATION, PROVIDER_HEALTH_OPERATION}
    finally:
        runtime.close()


def test_enqueue_skips_everything_when_evidence_is_fresh(tmp_path: Path) -> None:
    """Con evidencia vigente no se encola nada: el refresco no genera trabajo inútil."""
    runtime = _platform(tmp_path)
    try:
        result = enqueue_stale_health_checks(
            runtime, statuses=[_status(id="codex_cli", healthCheckedAt=_iso(5))]
        )

        assert result["enqueued"] == 0
        assert ExecutionRepository(runtime.connection).list_recent() == []
    finally:
        runtime.close()


def test_enqueue_never_raises_so_startup_is_never_blocked(tmp_path: Path) -> None:
    """Una operación no registrada se cuenta como fallo, no tumba el arranque del control plane."""
    runtime = _platform(tmp_path)
    try:
        runtime.execution_handlers = {}

        result = enqueue_stale_health_checks(
            runtime, statuses=[_status(id="codex_cli", kind="cli", healthCheckedAt=None)]
        )

        assert result["enqueued"] == 0
        assert result["failed"] == 1
        assert ExecutionRepository(runtime.connection).list_recent() == []
    finally:
        runtime.close()


def test_refresh_reads_real_statuses_when_none_are_supplied(tmp_path: Path) -> None:
    """Sin statuses explícitos consulta la misma fuente de verdad que la UI, sin sondear procesos."""
    with closing(open_sqlite_connection(tmp_path / "probe.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
    runtime = _platform(tmp_path)
    try:
        result = enqueue_stale_health_checks(runtime)

        assert "enqueued" in result and "considered" in result
        assert result["considered"] >= 0
    finally:
        runtime.close()


def test_worker_refresh_queues_health_checks_from_its_own_platform(tmp_path: Path) -> None:
    """El worker refresca por su cuenta: no monta routers, así que registra las operaciones él mismo."""
    from local_control_center.agents.runtime_health_refresh import refresh_from_worker

    sys.modules["faiss"] = None
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)

    result = refresh_from_worker(tmp_path / "platform.sqlite", tmp_path)

    assert result["failed"] == 0, result
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        queued = [
            row["operation"] for row in connection.execute("SELECT operation FROM operational_executions")
        ]
    assert set(queued) <= {CLI_HEALTH_OPERATION, PROVIDER_HEALTH_OPERATION}
    assert result["enqueued"] == len(queued)


def test_creating_the_app_never_queues_maintenance_work(tmp_path: Path) -> None:
    """`create_app` debe seguir siendo inerte: encolar desde ahí altera el FIFO de la cola real."""
    from fastapi.testclient import TestClient

    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    try:
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 200

        assert ExecutionRepository(runtime.connection).list_recent() == []
    finally:
        runtime.close()


def test_refresh_never_piles_up_while_the_previous_one_is_pending(tmp_path: Path) -> None:
    """Con el worker frenado, refrescar de nuevo no puede acumular ejecuciones identicas.

    Es el modo de falla observado en produccion: 608 health-checks encolados porque el ciclo
    encolaba cada 240 s aunque el gobernador tuviera todo en resource_wait.
    """
    runtime = _platform(tmp_path)
    try:
        statuses = [
            _status(id="codex_cli", kind="cli", healthCheckedAt=None),
            _status(id="gemini", kind="api", healthCheckedAt=None),
        ]
        first = enqueue_stale_health_checks(runtime, statuses=statuses)
        assert first["enqueued"] == 2

        for _ in range(5):
            repeat = enqueue_stale_health_checks(runtime, statuses=statuses)
            assert repeat["enqueued"] == 0, repeat
            assert repeat["pending"] == 2, repeat

        assert len(ExecutionRepository(runtime.connection).list_recent()) == 2
    finally:
        runtime.close()


def test_health_checks_are_admitted_while_the_host_is_busy(tmp_path: Path) -> None:
    """Un host ocupado no puede impedir el diagnóstico que repara al propio host.

    La evidencia de salud caduca a los 300 s y su único productor es esta operación encolada. Si el
    gobernador la rechaza por CPU/memoria/disco, la evidencia nunca se renueva, todo runtime
    configurado cae en ``health_check_required`` y la UI termina pidiendo reconfigurar lo que ya
    está configurado. Es el mismo callejón sin salida del TTL, un nivel más abajo.
    """
    from local_control_center.executions.workloads import operation_workload
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import (
        ResourceAdmissionRequest,
        ResourceSnapshot,
    )
    from local_control_center.host_resources.repository import ResourceRepository

    gib = 1024**3
    busy = ResourceSnapshot.test_snapshot(
        cpu_percent_1s=95,
        cpu_percent_30s=95,
        available_memory_bytes=1 * gib,
        disk_free_bytes={"C:": 1 * gib},
    )
    runtime = _platform(tmp_path)
    try:
        specs = {
            operation: runtime.execution_handlers[operation][0]
            for operation in (CLI_HEALTH_OPERATION, PROVIDER_HEALTH_OPERATION)
        }
        governor = HostResourceGovernor(runtime.connection)
        repository = ResourceRepository(runtime.connection)

        for index, (operation, spec) in enumerate(specs.items()):
            workload = operation_workload(runtime.connection, spec, {})
            decision = governor.admit(
                ResourceAdmissionRequest(
                    execution_id=f"health-{index}",
                    workload_class=workload,
                    owner_id="worker-one",
                ),
                snapshot=busy,
            )
            assert decision.status == "admitted", (operation, workload, decision.reason_code)
            if decision.lease is not None:
                repository.release(decision.lease.id, reason="test cleanup")

        # La contrapartida: el mismo host ocupado sigue frenando la inferencia real, que es cara.
        inference = runtime.execution_handlers["models.test_prompt"][0]
        blocked = governor.admit(
            ResourceAdmissionRequest(
                execution_id="inference-1",
                workload_class=operation_workload(runtime.connection, inference, {}),
                owner_id="worker-one",
            ),
            snapshot=busy,
        )
        assert blocked.status == "resource_wait", blocked.reason_code
    finally:
        runtime.close()
