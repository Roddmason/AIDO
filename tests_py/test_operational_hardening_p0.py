from __future__ import annotations

import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.cli_runtimes.base import RuntimeRequest
from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime
from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.security_policy.sandbox import RestrictedSubprocessSandbox
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workers.runtime import LocalWorkerRuntime, WorkerPreflight, WorkerSettings


def _runtime(tmp_path: Path) -> ControlCenterRuntime:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    runtime.init()
    SettingsRepository(runtime.connection).set_value("worker.autostart", "general", None, False)
    return runtime


def test_overview_stays_responsive_while_an_api_operation_is_slow(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    app = create_app(runtime=runtime, static_dir=None)
    entered = threading.Event()
    release = threading.Event()

    @app.get("/api/v1/_p0-test/slow")
    async def slow_operation() -> dict[str, str]:
        entered.set()
        await anyio.to_thread.run_sync(release.wait)
        return {"status": "completed"}

    with TestClient(app) as client:
        slow_thread = threading.Thread(
            target=lambda: client.get("/api/v1/_p0-test/slow"),
            name="p0-slow-request",
        )
        slow_thread.start()
        assert entered.wait(timeout=2)
        release_timer = threading.Timer(0.75, release.set)
        release_timer.start()
        started = time.perf_counter()
        response = client.get("/api/v1/overview")
        elapsed = time.perf_counter() - started
        release.set()
        slow_thread.join(timeout=2)
        release_timer.cancel()

    runtime.close()
    assert response.status_code == 200
    assert elapsed < 0.3


def test_healthz_is_served_while_the_overview_is_still_building(tmp_path: Path, monkeypatch) -> None:
    import local_control_center.api as api_module

    runtime = _runtime(tmp_path)
    app = create_app(runtime=runtime, static_dir=None)
    entered = threading.Event()
    real_build = api_module.build_overview_from_connection

    def slow_build(**kwargs):
        entered.set()
        time.sleep(1.5)
        return real_build(**kwargs)

    monkeypatch.setattr(api_module, "build_overview_from_connection", slow_build)
    overview_responses = []
    with TestClient(app) as client:
        overview_thread = threading.Thread(
            target=lambda: overview_responses.append(client.get("/api/v1/overview")),
            name="p0-slow-overview",
        )
        overview_thread.start()
        assert entered.wait(timeout=5)
        started = time.perf_counter()
        response = client.get("/healthz")
        elapsed = time.perf_counter() - started
        overview_still_building = overview_thread.is_alive()
        overview_thread.join(timeout=10)

    runtime.close()
    assert response.status_code == 200
    assert overview_still_building
    assert elapsed < 0.5
    assert overview_responses[0].status_code == 200


def test_fastapi_does_not_own_an_in_process_worker(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    app = create_app(runtime=runtime, static_dir=None)

    with TestClient(app):
        assert not hasattr(app.state, "worker_runtime")
        assert not hasattr(runtime, "local_worker_runtime")

    runtime.close()


def test_two_worker_connections_elect_exactly_one_durable_leader(tmp_path: Path) -> None:
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    db_path = tmp_path / "platform.sqlite"
    with (
        closing(open_sqlite_connection(db_path)) as first,
        first,
        closing(open_sqlite_connection(db_path)) as second,
        second,
    ):
        initialize_platform_schema(first)
        first_lease = WorkerLeadershipRepository(first).acquire(owner_id="worker-one", lease_seconds=30)
        second_lease = WorkerLeadershipRepository(second).acquire(owner_id="worker-two", lease_seconds=30)

    assert first_lease.acquired is True
    assert first_lease.role == "leader"
    assert second_lease.acquired is False
    assert second_lease.role == "standby"
    assert first_lease.fencing_token > 0


def test_pause_does_not_claim_to_cancel_an_active_batch(tmp_path: Path, monkeypatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingWorker:
        def __init__(self, *, db_path: Path) -> None:
            self.db_path = db_path

        def run_batch(
            self,
            *,
            worker_count: int,
            max_jobs: int,
            worker_id: str | None = None,
            fencing_token: int | None = None,
        ) -> list[dict[str, object]]:
            entered.set()
            release.wait(timeout=2)
            return []

    monkeypatch.setattr("local_control_center.workers.runtime.ConcurrentWorker", BlockingWorker)
    worker = LocalWorkerRuntime(
        db_path=tmp_path / "platform.sqlite",
        cwd=tmp_path,
        settings=WorkerSettings(autostart=False, max_concurrent_jobs=1),
    )
    monkeypatch.setattr(
        worker,
        "preflight",
        lambda: WorkerPreflight(ok=True, reason="ok", runtime_ids=["test"], gitleaks_executable="test"),
    )
    batch_thread = threading.Thread(target=worker.run_once, name="p0-active-batch")
    batch_thread.start()
    assert entered.wait(timeout=2)

    status = worker.pause()

    assert status["paused"] is True
    assert status["inFlightJobs"] == 1
    assert batch_thread.is_alive()
    release.set()
    batch_thread.join(timeout=2)


def test_host_capacity_can_defer_a_heavy_workload_without_failing_it(tmp_path: Path) -> None:
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot

    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        snapshot = ResourceSnapshot.test_snapshot(
            cpu_percent_1s=90,
            available_memory_bytes=4 * 1024**3,
            disk_free_bytes=100 * 1024**3,
        )
        decision = HostResourceGovernor(connection).admit(
            ResourceAdmissionRequest(
                execution_id="execution-heavy",
                workload_class="build_heavy",
                owner_id="worker-one",
            ),
            snapshot=snapshot,
        )

    assert decision.status == "resource_wait"
    assert decision.reason_code == "hard_memory_floor"


def test_codex_profile_does_not_pin_an_obsolete_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model_configuration_required"):
        CodexCliRuntime(executable="codex").build_command(
            RuntimeRequest(
                runtime="codex_cli",
                workspaceId="workspace-one",
                workspacePath=str(tmp_path),
                prompt="Inspect the repository without editing.",
                profile="codex_gpt55_developer",
                envPolicy={"permissionProfile": "plan"},
            )
        )


def test_productive_sandbox_execution_returns_managed_process_evidence(
    tmp_path: Path, low_impact_host_policy
) -> None:
    low_impact_host_policy(tmp_path / "default-runtime.sqlite")
    result = RestrictedSubprocessSandbox().execute(
        argv=[sys.executable, "-c", "print('managed')"],
        cwd=str(tmp_path),
        workspace_path=str(tmp_path),
        timeout_seconds=5,
    )

    assert result["returnCode"] == 0
    assert result["managedProcessId"]
    assert result["workloadClass"] == "agent_cli"
    assert result["peakMemoryBytes"] >= 0
