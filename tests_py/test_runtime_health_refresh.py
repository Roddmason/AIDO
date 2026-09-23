"""Tests del refresco automático de evidencia de salud: la evidencia caduca a los 300 s y el único
productor es una operación encolada, así que sin refresco automático un sistema bien configurado se
reporta bloqueado a los pocos minutos y después de cada reinicio del control plane.

@author Rodrigo Mason
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents.runtime_health_refresh import (
    CLI_HEALTH_OPERATION,
    PROVIDER_HEALTH_OPERATION,
    HealthRefreshBackoff,
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


def test_the_refresh_cadence_cannot_let_evidence_expire() -> None:
    """La cadencia del ciclo y el umbral de vencimiento no pueden ser el mismo numero.

    Medido en la instalacion real: con ambos en 240 s, un runtime cuya evidencia cruza el umbral
    justo despues de un ciclo espera otros 240 s, llega a 480 s y vence (TTL 300). Se observaron
    gemini 365 s, nvidia_nim 357 s y omniroute 318 s, todos reportando `health_check_required`
    con la configuracion intacta. La invariante es aritmetica: umbral + espera del ciclo + tiempo
    de ejecucion tiene que caber dentro del TTL.
    """
    from local_control_center.agents.runtime_health_refresh import (
        REFRESH_POLL_SECONDS,
        REFRESH_STALENESS_SECONDS,
    )

    budget = HEALTH_EVIDENCE_TTL_SECONDS - (REFRESH_STALENESS_SECONDS + REFRESH_POLL_SECONDS)

    assert budget >= 60, (
        f"solo quedan {budget}s para encolar, admitir y ejecutar el health-check antes de que "
        f"la evidencia venza (umbral={REFRESH_STALENESS_SECONDS}s, ciclo={REFRESH_POLL_SECONDS}s)"
    )
    assert REFRESH_POLL_SECONDS < REFRESH_STALENESS_SECONDS, (
        "el ciclo tiene que mirar mas seguido de lo que tarda la evidencia en ponerse rancia"
    )


def test_evidence_just_past_the_threshold_is_selected() -> None:
    """Cruzar el umbral basta para entrar al proximo ciclo, sin esperar al vencimiento."""
    from local_control_center.agents.runtime_health_refresh import REFRESH_STALENESS_SECONDS

    assert needs_refresh(_status(healthCheckedAt=_iso(REFRESH_STALENESS_SECONDS + 1))) is True
    assert needs_refresh(_status(healthCheckedAt=_iso(REFRESH_STALENESS_SECONDS - 30))) is False


@pytest.mark.parametrize("pending_status", ["queued", "resource_wait"])
def test_pending_health_target_does_not_block_other_runtimes(tmp_path: Path, pending_status: str) -> None:
    runtime = _platform(tmp_path)
    try:
        codex = _status(healthCheckedAt=None)
        assert enqueue_stale_health_checks(runtime, statuses=[codex])["enqueued"] == 1
        runtime.connection.execute("UPDATE operational_executions SET status=?", (pending_status,))
        statuses = [
            codex,
            _status(id="claude_code_cli", healthCheckedAt=None),
            _status(id="gemini", kind="api", healthCheckedAt=None),
        ]

        result = enqueue_stale_health_checks(runtime, statuses=statuses)

        assert result["enqueued"] == 2, result
        assert set(result["runtimes"]) == {"claude_code_cli", "gemini"}
        assert result["pending"] == 1
        repeat = enqueue_stale_health_checks(runtime, statuses=statuses)
        assert repeat["enqueued"] == 0, repeat
        assert repeat["pending"] == 3
        assert len(ExecutionRepository(runtime.connection).list_recent()) == 3
    finally:
        runtime.close()


def test_refresh_deduplicates_targets_within_one_batch_and_allows_terminal_retry(tmp_path: Path) -> None:
    runtime = _platform(tmp_path)
    try:
        statuses = [_status(healthCheckedAt=None)] * 2
        first = enqueue_stale_health_checks(runtime, statuses=statuses)
        assert first["enqueued"] == 1, first
        runtime.connection.execute("UPDATE operational_executions SET status='completed'")

        result = enqueue_stale_health_checks(
            runtime, statuses=statuses, now=datetime.now(UTC) + timedelta(seconds=61)
        )

        assert result["enqueued"] == 1, result
        assert result["pending"] == 0
        assert len(ExecutionRepository(runtime.connection).list_recent()) == 2
    finally:
        runtime.close()


def _native_health_fixture(tmp_path: Path, monkeypatch):
    from local_control_center.agents.model_gateway_api import create_router
    from local_control_center.agents.runtime_registry import RuntimeRegistry
    from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

    runtime = _platform(tmp_path)
    create_router(platform=runtime, require_write=lambda _request: None)
    repo = RuntimeConfigRepository(runtime.connection)
    repo.upsert_installation(
        {
            "runtimeId": "codex_cli",
            "kind": "cli",
            "executablePath": "C:/fake/codex.exe",
            "detectedVersion": "0.155.1",
            "enabled": True,
        }
    )
    account = next(item for item in repo.list_runtime_accounts("codex_cli") if item["isDefault"])
    previous = _iso(181)
    account = repo.update_runtime_account(
        account["id"], {"enabled": True, "healthStatus": "healthy", "lastValidationAt": previous}
    )
    monkeypatch.delenv("AIDO_CODEX_COMMAND", raising=False)
    monkeypatch.setattr(
        RuntimeRegistry,
        "detect",
        lambda _self, runtime_id, **_: {
            "runtime": runtime_id,
            "status": "installed",
            "executable": "C:/fake/codex.exe",
            "version": "0.155.1",
            "message": "fake version probe",
        },
    )
    monkeypatch.setattr(
        RuntimeRegistry,
        "health_check",
        lambda _self, runtime_id, **_: {"runtime": runtime_id, "status": "healthy", "message": "fake"},
    )
    return runtime, repo, account, previous


@pytest.mark.parametrize("verdict", ["authenticated", "unauthenticated", "unknown"])
def test_explicit_cli_health_refreshes_native_auth_before_evidence_expires(
    tmp_path: Path, monkeypatch, verdict: str
) -> None:
    from starlette.requests import Request

    from local_control_center.agents.runtime_registry import RuntimeRegistry

    runtime, repo, account, previous = _native_health_fixture(tmp_path, monkeypatch)
    calls = []

    def validate(_self, runtime_id, **_):
        calls.append(runtime_id)
        return {"runtime": runtime_id, "status": verdict, "message": "fake native auth verdict"}

    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", validate)
    try:
        assert needs_refresh(_status(healthCheckedAt=previous))
        handler = runtime.execution_handlers[CLI_HEALTH_OPERATION][1]
        asyncio.run(handler("codex_cli", Request({"type": "http", "method": "POST", "path": "/"})))

        updated = repo.get_runtime_account(account["id"])
        assert calls == ["codex_cli"]
        if verdict == "authenticated":
            assert updated["lastValidationAt"] > previous
            assert updated["healthStatus"] == "healthy"
            assert not needs_refresh(_status(healthCheckedAt=updated["lastValidationAt"]))
        elif verdict == "unauthenticated":
            assert updated["lastValidationAt"] is None
            assert updated["healthStatus"] == "unauthenticated"
        else:
            assert updated["lastValidationAt"] == previous
            assert updated["healthStatus"] == "healthy"
    finally:
        runtime.close()


def test_cli_health_does_not_stamp_auth_after_configuration_changes(tmp_path: Path, monkeypatch) -> None:
    from starlette.requests import Request

    from local_control_center.agents.runtime_registry import RuntimeRegistry

    runtime, repo, account, _previous = _native_health_fixture(tmp_path, monkeypatch)
    calls = []

    def validate(_self, runtime_id, **_):
        calls.append(runtime_id)
        repo.update_runtime_account(
            account["id"], {"enabled": False, "healthStatus": "unknown", "lastValidationAt": None}
        )
        return {"runtime": runtime_id, "status": "authenticated", "message": "old configuration"}

    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", validate)
    try:
        handler = runtime.execution_handlers[CLI_HEALTH_OPERATION][1]
        asyncio.run(handler("codex_cli", Request({"type": "http", "method": "POST", "path": "/"})))

        updated = repo.get_runtime_account(account["id"])
        assert calls == ["codex_cli"]
        assert updated["enabled"] is False
        assert updated["lastValidationAt"] is None
        assert updated["healthStatus"] == "unknown"
    finally:
        runtime.close()


def test_status_read_keeps_preventive_refresh_due_without_running_native_auth(
    tmp_path: Path, monkeypatch
) -> None:
    from local_control_center.agents.runtime_registry import RuntimeRegistry
    from local_control_center.agents.runtime_status import RuntimeStatusService

    runtime, repo, account, previous = _native_health_fixture(tmp_path, monkeypatch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("status reads cannot run native CLI probes")

    monkeypatch.setattr(RuntimeRegistry, "detect", forbidden)
    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", forbidden)
    try:
        RuntimeStatusService(runtime.connection).list_provider_statuses()

        assert repo.get_runtime_account(account["id"])["lastValidationAt"] == previous
        assert needs_refresh(_status(healthCheckedAt=previous))
    finally:
        runtime.close()


@pytest.mark.parametrize("entrypoint", ["service", "registered_handler"])
def test_cli_health_rejects_executable_changed_during_detection(
    tmp_path: Path, monkeypatch, entrypoint: str
) -> None:
    from starlette.requests import Request

    from local_control_center.agents.runtime_registry import RuntimeRegistry
    from local_control_center.agents.runtime_status import RuntimeStatusService

    runtime, repo, account, previous = _native_health_fixture(tmp_path, monkeypatch)
    auth_commands = []
    detect_commands = []

    def detect(_self, runtime_id, *, executable=None):
        detect_commands.append(executable)
        if len(detect_commands) == 1:
            assert executable == "C:/fake/codex.exe"
            repo.upsert_installation(
                {
                    "runtimeId": runtime_id,
                    "executablePath": "C:/replacement/codex.exe",
                    "detectedVersion": "0.155.2",
                }
            )
        return {
            "runtime": runtime_id,
            "status": "installed",
            "executable": executable,
            "version": "0.156.0",
            "message": "old command detection completed after configuration changed",
        }

    def validate(_self, runtime_id, *, executable=None):
        auth_commands.append(executable)
        return {"runtime": runtime_id, "status": "authenticated", "message": "old command authenticated"}

    monkeypatch.setattr(RuntimeRegistry, "detect", detect)
    monkeypatch.setattr(RuntimeRegistry, "validate_native_auth", validate)
    try:
        result = None
        if entrypoint == "registered_handler":
            handler = runtime.execution_handlers[CLI_HEALTH_OPERATION][1]
            result = asyncio.run(
                handler("codex_cli", Request({"type": "http", "method": "POST", "path": "/"}))
            )
        else:
            RuntimeStatusService(
                runtime.connection,
                allow_probes=True,
                probe_runtime_ids={"codex_cli"},
                force_native_auth_refresh=True,
            ).list_provider_statuses()

        assert repo.get_runtime_account(account["id"])["lastValidationAt"] == previous
        assert auth_commands == []
        installation = repo.get_installation("codex_cli")
        assert installation["executablePath"] == "C:/replacement/codex.exe"
        assert installation["detectedVersion"] == "0.155.2"
        if result is not None:
            assert result["health"]["status"] == "unknown"
            assert "configuration changed" in result["health"]["message"].lower()
    finally:
        runtime.close()


def _iso_at(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_unproductive_refresh_backs_off_exponentially_and_fresh_evidence_resets_it(tmp_path: Path) -> None:
    """Con el token vencido el check termina sin renovar la evidencia: no puede reencolarse cada ciclo.

    Medido en vivo: 233 checks de claude_code_cli contra 62 de codex_cli en la misma ventana.
    """
    runtime = _platform(tmp_path)
    try:
        start = datetime.now(UTC)
        stale = [_status(id="claude_code_cli", kind="cli", healthCheckedAt=None)]

        def refresh_at(seconds: float, statuses: list[dict] | None = None) -> dict:
            result = enqueue_stale_health_checks(
                runtime, statuses=statuses or stale, now=start + timedelta(seconds=seconds)
            )
            runtime.connection.execute("UPDATE operational_executions SET status='completed'")
            return result

        assert refresh_at(0)["enqueued"] == 1
        cooling = refresh_at(30)
        assert cooling["enqueued"] == 0
        assert cooling["coolingDown"] == ["claude_code_cli"]
        assert refresh_at(61)["enqueued"] == 1
        assert refresh_at(360)["enqueued"] == 0
        assert refresh_at(362)["enqueued"] == 1
        assert refresh_at(1261)["enqueued"] == 0
        assert refresh_at(1263)["enqueued"] == 1
        assert refresh_at(2164)["enqueued"] == 1

        fresh = [
            _status(
                id="claude_code_cli",
                kind="cli",
                healthStatus="healthy",
                healthCheckedAt=_iso_at(start + timedelta(seconds=2170)),
            )
        ]
        assert refresh_at(2175, fresh)["enqueued"] == 0
        assert refresh_at(2180)["enqueued"] == 1
    finally:
        runtime.close()


def test_fresh_but_unhealthy_evidence_does_not_reset_the_backoff(tmp_path: Path) -> None:
    """Cada health check escribe su marca aunque el runtime quede unhealthy: esa marca no es un éxito.

    ``ProviderAccountsRepository.record_health_check`` escribe ``lastHealthCheckAt`` para cualquier
    desenlace y readiness exige además ``healthStatus == "healthy"``; reiniciar el cooldown con una
    marca fresca pero unhealthy devolvería el objetivo al primer escalón en cada ciclo.
    """
    runtime = _platform(tmp_path)
    try:
        start = datetime.now(UTC)
        backoff = HealthRefreshBackoff()
        stale = [_status(id="claude_code_cli", kind="cli", healthCheckedAt=None)]
        unhealthy_fresh = [
            _status(
                id="claude_code_cli",
                kind="cli",
                healthStatus="unhealthy",
                healthCheckedAt=_iso_at(start + timedelta(seconds=65)),
            )
        ]

        def refresh_at(seconds: float, statuses: list[dict]) -> dict:
            result = enqueue_stale_health_checks(
                runtime, statuses=statuses, now=start + timedelta(seconds=seconds), backoff=backoff
            )
            runtime.connection.execute("UPDATE operational_executions SET status='completed'")
            return result

        assert refresh_at(0, stale)["enqueued"] == 1
        assert refresh_at(61, stale)["enqueued"] == 1
        assert refresh_at(70, unhealthy_fresh)["enqueued"] == 0
        cooling = refresh_at(300, stale)
        assert cooling["enqueued"] == 0
        assert cooling["coolingDown"] == ["claude_code_cli"]
        assert refresh_at(362, stale)["enqueued"] == 1
    finally:
        runtime.close()


def test_a_cancelled_refresh_counts_as_unproductive(tmp_path: Path) -> None:
    """Una ejecución cancelada no renovó la evidencia: escala el cooldown igual que un fallo."""
    runtime = _platform(tmp_path)
    try:
        start = datetime.now(UTC)
        backoff = HealthRefreshBackoff()
        stale = [_status(id="codex_cli", kind="cli", healthCheckedAt=None)]

        def refresh_at(seconds: float) -> dict:
            result = enqueue_stale_health_checks(
                runtime, statuses=stale, now=start + timedelta(seconds=seconds), backoff=backoff
            )
            runtime.connection.execute("UPDATE operational_executions SET status='cancelled'")
            return result

        assert refresh_at(0)["enqueued"] == 1
        assert refresh_at(30)["coolingDown"] == ["codex_cli"]
        assert refresh_at(61)["enqueued"] == 1
        assert refresh_at(300)["coolingDown"] == ["codex_cli"]
    finally:
        runtime.close()
