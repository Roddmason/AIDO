from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.jobs_approvals.worker import ConcurrentWorker
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import iso_after_seconds, utc_now
from local_control_center.threads.repository import ThreadsRepository

GIB = 1024**3


def _healthy_snapshot(**overrides) -> ResourceSnapshot:
    values = {
        "cpu_percent_1s": 20,
        "cpu_percent_30s": 25,
        "available_memory_bytes": 32 * GIB,
        "disk_free_bytes": {"test": 200 * GIB},
    }
    values.update(overrides)
    return ResourceSnapshot.test_snapshot(**values)


def _request(execution_id: str, workload_class: str, *, job_id: str | None = None):
    return ResourceAdmissionRequest(
        execution_id=execution_id,
        workload_class=workload_class,
        owner_id="worker-one",
        job_id=job_id,
    )


def test_resource_schema_is_idempotent_and_contains_durable_contracts(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase_rows = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 60"
        ).fetchone()[0]

    assert tables >= {
        "resource_leases",
        "resource_usage_samples",
        "resource_violations",
        "resource_admission_decisions",
    }
    assert phase_rows == 1


def test_memory_and_disk_floors_defer_work_instead_of_failing(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)

        hard = governor.admit(
            _request("hard-memory", "build_heavy"),
            snapshot=_healthy_snapshot(available_memory_bytes=4 * GIB),
        )
        soft = governor.admit(
            _request("soft-memory", "build_heavy"),
            snapshot=_healthy_snapshot(available_memory_bytes=12 * GIB),
        )
        disk = governor.admit(
            _request("disk-floor", "build_heavy"),
            snapshot=_healthy_snapshot(disk_free_bytes={"test": 40 * GIB}),
        )

    assert (hard.status, hard.reason_code) == ("resource_wait", "hard_memory_floor")
    assert (soft.status, soft.reason_code) == ("resource_wait", "minimum_free_memory")
    assert (disk.status, disk.reason_code) == ("resource_wait", "minimum_free_disk")


def test_only_one_heavy_workload_is_admitted_globally(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        first = governor.admit(_request("build-one", "build_heavy"), snapshot=_healthy_snapshot())
        second = governor.admit(_request("cli-two", "agent_cli"), snapshot=_healthy_snapshot())

    assert first.status == "admitted"
    assert first.lease is not None
    assert second.status == "resource_wait"
    assert second.reason_code == "heavy_workload_capacity"


def test_two_remote_llm_workloads_coexist_but_third_waits(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        decisions = [
            governor.admit(_request(f"remote-{index}", "remote_llm_light"), snapshot=_healthy_snapshot())
            for index in range(1, 4)
        ]

    assert [decision.status for decision in decisions] == ["admitted", "admitted", "resource_wait"]
    assert decisions[-1].reason_code == "light_workload_capacity"


def test_aggregate_cpu_budget_blocks_light_work_beside_full_budget_build(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        build = governor.admit(_request("build", "build_heavy"), snapshot=_healthy_snapshot())
        light = governor.admit(_request("qa", "qa_light"), snapshot=_healthy_snapshot())
        assert build.status == "admitted"
        assert (light.status, light.reason_code) == ("resource_wait", "aggregate_cpu_budget")
        governor.release(build.lease.id, reason="test_completed")
        assert governor.admit(_request("qa", "qa_light"), snapshot=_healthy_snapshot()).status == "admitted"


def test_aggregate_memory_reservations_preserve_control_plane_headroom(tmp_path: Path) -> None:
    """Las reservas vivas no pueden comerse el margen: 20,5 GiB - piso 16 = 4,5 < agente 1 + QA 4."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        snapshot = _healthy_snapshot(available_memory_bytes=int(20.5 * GIB))
        assert governor.admit(_request("cli", "agent_cli"), snapshot=snapshot).status == "admitted"
        second = governor.admit(_request("qa", "qa_light"), snapshot=snapshot)
        assert (second.status, second.reason_code) == ("resource_wait", "aggregate_memory_budget")


def test_aggregate_budget_allows_cli_and_one_qa_with_sufficient_headroom(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        assert governor.admit(_request("cli", "agent_cli"), snapshot=_healthy_snapshot()).status == "admitted"
        assert governor.admit(_request("qa", "qa_light"), snapshot=_healthy_snapshot()).status == "admitted"
        third = governor.admit(_request("remote", "remote_llm_light"), snapshot=_healthy_snapshot())
        assert third.status == "resource_wait"


def test_control_plane_cpu_is_not_free_when_admitting_collector(tmp_path):
    """A control-plane root plus CLI plus collector would reserve 85%, above the 75% policy."""
    with closing(open_sqlite_connection(tmp_path / "scope.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        snapshot = _healthy_snapshot(available_memory_bytes=48 * GIB)
        for identity, workload in (
            ("api", "control_plane"),
            ("cli", "agent_cli"),
        ):
            assert governor.admit(_request(identity, workload), snapshot=snapshot).lease
        decision = governor.admit(_request("collector", "qa_light"), snapshot=snapshot)
        assert decision.lease is None
        assert decision.reason_code == "aggregate_cpu_budget"


def test_unreal_and_heavy_conflicts_have_specific_reasons(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        gpu = governor.admit(
            _request("gpu-one", "local_gpu_model"),
            snapshot=_healthy_snapshot(unreal_editor_running=True),
        )
        build = governor.admit(_request("build-one", "build_heavy"), snapshot=_healthy_snapshot())
        browser = governor.admit(_request("browser-one", "browser_test"), snapshot=_healthy_snapshot())

    assert gpu.reason_code == "unreal_local_gpu_conflict"
    assert build.status == "admitted"
    assert browser.reason_code == "browser_build_conflict"


def test_unreal_cook_is_exclusive(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        light = governor.admit(_request("remote-one", "remote_llm_light"), snapshot=_healthy_snapshot())
        cook = governor.admit(_request("cook-one", "unreal_cook"), snapshot=_healthy_snapshot())

    assert light.status == "admitted"
    assert cook.status == "resource_wait"
    assert cook.reason_code == "unreal_cook_exclusive"


def test_expired_lease_is_recovered_and_capacity_is_reusable(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        admitted = governor.admit(
            ResourceAdmissionRequest(
                execution_id="build-expired",
                workload_class="build_heavy",
                owner_id="worker-one",
                lease_seconds=1,
            ),
            snapshot=_healthy_snapshot(),
        )
        recovered = governor.recover_expired(now_iso="2099-01-01T00:00:00.000Z")
        replacement = governor.admit(
            _request("build-replacement", "build_heavy"), snapshot=_healthy_snapshot()
        )

    assert admitted.lease is not None
    assert [lease.release_reason for lease in recovered] == ["lease_expired"]
    assert replacement.status == "admitted"


def test_resource_wait_job_is_durable_and_can_be_reevaluated(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="AIDO", path=tmp_path, template_id="other"
        )
        job = JobsRepository(connection).create_job(project_id=project["id"], kind="thread.product_loop.run")[
            "job"
        ]
        governor = HostResourceGovernor(connection)
        waiting = governor.admit(
            _request(job["id"], "agent_cli", job_id=job["id"]),
            snapshot=_healthy_snapshot(available_memory_bytes=4 * GIB),
        )
        waiting_job = JobsRepository(connection).get_job(job["id"])
        reevaluated = governor.reevaluate_waiting(snapshot=_healthy_snapshot())
        queued_job = JobsRepository(connection).get_job(job["id"])

    assert waiting.status == "resource_wait"
    assert waiting_job["status"] == "resource_wait"
    assert reevaluated[0].status == "admitted"
    assert queued_job["status"] == "queued"


def test_usage_sample_retention_is_bounded(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        repository = ResourceRepository(connection)
        now = datetime.now(UTC)
        repository.record_sample(_healthy_snapshot(sampled_at=(now - timedelta(days=8)).isoformat()))
        recent = repository.record_sample(_healthy_snapshot(sampled_at=now.isoformat()))
        deleted = repository.prune_samples(
            retention_seconds=7 * 24 * 3600,
            now_iso=now.isoformat(),
        )
        samples = repository.list_samples(limit=10)

    assert deleted == 1
    assert [sample.id for sample in samples] == [recent.id]


def test_resource_settings_reject_unsafe_values() -> None:
    max_heavy = descriptor_for("resources.maxHeavyWorkloads")
    memory_floor = descriptor_for("resources.minFreeMemoryGiB")
    interval = descriptor_for("resources.sampleIntervalSeconds")
    assert max_heavy is not None and memory_floor is not None and interval is not None

    assert validate_value(max_heavy, 1) == 1
    with pytest.raises(ValueError):
        validate_value(max_heavy, 16)
    with pytest.raises(ValueError):
        validate_value(memory_floor, -1)
    with pytest.raises(ValueError):
        validate_value(interval, 0)


def test_resource_settings_api_exposes_bounds_and_rejects_inconsistent_memory_floor(
    tmp_path: Path,
) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    with TestClient(app) as client:
        token = client.get("/api/v1/security/handshake").json()["token"]
        settings = client.get("/api/v1/settings").json()["general"]
        rejected = client.put(
            "/api/v1/settings/resources.hardFreeMemoryGiB",
            headers={"X-Local-Control-Token": token},
            json={"scope": "general", "value": 32},
        )
    runtime.close()

    max_heavy = next(item for item in settings if item["key"] == "resources.maxHeavyWorkloads")
    assert max_heavy["minimum"] == 1
    assert max_heavy["maximum"] == 1
    assert rejected.status_code == 422
    assert "cannot exceed" in rejected.json()["detail"]


@pytest.mark.real_host_resources
def test_psutil_probe_collects_required_host_fields(tmp_path: Path) -> None:
    snapshot = HostResourceProbe(relevant_paths=[tmp_path]).sample(cpu_interval_seconds=0)

    assert snapshot.logical_processors >= 1
    assert snapshot.total_memory_bytes > 0
    assert snapshot.available_memory_bytes > 0
    assert snapshot.disk_free_bytes
    assert snapshot.disk_read_bytes_per_second >= 0
    assert snapshot.disk_write_bytes_per_second >= 0
    assert isinstance(snapshot.active_workloads, list)


def test_worker_defers_then_reevaluates_job_and_releases_resource_lease(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="AIDO", path=tmp_path, template_id="other"
        )
        job = JobsRepository(connection).create_job(project_id=project["id"], kind="thread.product_loop.run")[
            "job"
        ]

    deferred = ConcurrentWorker(
        db_path=db_path,
        resource_snapshot=_healthy_snapshot(available_memory_bytes=4 * GIB),
    ).run_once(worker_id="worker-one")
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        waiting_job = JobsRepository(connection).get_job(job["id"])

    monkeypatch.setattr(
        "local_control_center.jobs_approvals.worker.execute_job",
        lambda *_args, **_kwargs: {"summary": "completed", "metadata": {}},
    )
    completed = ConcurrentWorker(
        db_path=db_path,
        resource_snapshot=_healthy_snapshot(),
    ).run_once(worker_id="worker-one")
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        final_job = JobsRepository(connection).get_job(job["id"])
        active_leases = ResourceRepository(connection).active_leases()

    assert deferred is None
    assert waiting_job["status"] == "resource_wait"
    assert completed is not None
    assert final_job["status"] == "completed"
    assert active_leases == []


def test_hard_floor_records_cancellation_request_for_nonessential_workload(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        admitted = governor.admit(_request("remote-one", "remote_llm_light"), snapshot=_healthy_snapshot())
        violations = governor.violations_for_snapshot(_healthy_snapshot(available_memory_bytes=4 * GIB))

    assert admitted.status == "admitted"
    assert len(violations) == 1
    assert violations[0].action == "cancel_non_essential_workload"
    assert violations[0].execution_id == "remote-one"


def test_hard_floor_above_the_floor_never_calls_usage_source(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        governor.admit(_request("remote", "remote_llm_light"), snapshot=_healthy_snapshot())

        def boom():
            raise AssertionError("usage_source must not run above the hard floor")

        violations = governor.violations_for_snapshot(_healthy_snapshot(), usage_source=boom)

    assert violations == []


def test_hard_floor_never_evicts_essential_leases(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        governor.admit(_request("control", "control_plane"), snapshot=_healthy_snapshot())
        violations = governor.violations_for_snapshot(_healthy_snapshot(available_memory_bytes=4 * GIB))

    assert violations == []


def test_hard_floor_evicts_the_lease_that_exceeds_its_reserve_first(tmp_path: Path) -> None:
    """Con dos candidatas, la primera violación cae sobre la que más excede su reserva medida."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        under = governor.admit(
            _request("under-reserve", "remote_llm_light"), snapshot=_healthy_snapshot()
        ).lease
        over = governor.admit(_request("over-reserve", "agent_cli"), snapshot=_healthy_snapshot()).lease
        # remote_llm_light reserva 0,5 GiB (uso 1 la excede en 0,5); agent_cli reserva 1 (uso 2 la excede en 1).
        usage = {under.id: 1 * GIB, over.id: 2 * GIB}

        violations = governor.violations_for_snapshot(
            _healthy_snapshot(available_memory_bytes=4 * GIB), usage_source=lambda: usage
        )

    assert len(violations) == 1
    assert violations[0].execution_id == "over-reserve"
    assert violations[0].lease_id == over.id
    assert "2.0 GiB used" in violations[0].reason
    assert "1.0 GiB reserved" in violations[0].reason


def test_hard_floor_with_usage_source_skips_leases_without_live_processes(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        governor.admit(_request("idle-lease", "remote_llm_light"), snapshot=_healthy_snapshot())

        violations = governor.violations_for_snapshot(
            _healthy_snapshot(available_memory_bytes=4 * GIB), usage_source=lambda: {}
        )

    assert violations == []


def test_hard_floor_without_usage_source_evicts_the_most_recent_lease(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        governor.admit(
            _request("older", "remote_llm_light"),
            snapshot=_healthy_snapshot(),
            now_iso="2026-01-01T00:00:00.000Z",
        )
        newer = governor.admit(
            _request("newer", "remote_llm_light"),
            snapshot=_healthy_snapshot(),
            now_iso="2026-01-01T00:00:01.000Z",
        ).lease

        violations = governor.violations_for_snapshot(
            _healthy_snapshot(available_memory_bytes=4 * GIB), now_iso="2026-01-01T00:00:02.000Z"
        )

    assert len(violations) == 1
    assert violations[0].execution_id == "newer"
    assert violations[0].lease_id == newer.id


def test_hard_floor_usage_source_error_falls_back_to_the_most_recent_lease(tmp_path: Path) -> None:
    """Sin medición no se prioriza por exceso, pero se sigue desalojando: nunca se deja de contener."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        governor.admit(
            _request("older", "remote_llm_light"),
            snapshot=_healthy_snapshot(),
            now_iso="2026-01-01T00:00:00.000Z",
        )
        governor.admit(
            _request("newer", "remote_llm_light"),
            snapshot=_healthy_snapshot(),
            now_iso="2026-01-01T00:00:01.000Z",
        )

        def boom():
            raise RuntimeError("psutil exploded")

        violations = governor.violations_for_snapshot(
            _healthy_snapshot(available_memory_bytes=4 * GIB),
            usage_source=boom,
            now_iso="2026-01-01T00:00:02.000Z",
        )

    assert [violation.execution_id for violation in violations] == ["newer"]


def test_hard_floor_ranks_by_the_reserve_recorded_in_each_lease(tmp_path: Path) -> None:
    """Una lease previa a la fase 81 (reserva NULL) cuenta por su tope, igual que en la admisión."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        legacy = governor.admit(_request("legacy", "agent_cli"), snapshot=_healthy_snapshot()).lease
        current = governor.admit(_request("current", "remote_llm_light"), snapshot=_healthy_snapshot()).lease
        connection.execute(
            "UPDATE resource_leases SET memory_request_bytes = NULL WHERE id = ?", (legacy.id,)
        )
        # La vieja (sin reserva registrada) cuenta por su tope de 8: sus 3 GiB no lo exceden. Con la
        # reserva del perfil actual (1 GiB) excedería por 2 y ganaría; la actual excede su 0,5 por 1.
        usage = {legacy.id: 3 * GIB, current.id: int(1.5 * GIB)}

        violations = governor.violations_for_snapshot(
            _healthy_snapshot(available_memory_bytes=4 * GIB), usage_source=lambda: usage
        )

    assert [violation.execution_id for violation in violations] == ["current"]


def test_hard_floor_grace_period_defers_the_next_eviction(tmp_path: Path) -> None:
    """Repetir dentro de la gracia no agrega una segunda víctima; pasada la gracia, sigue con la otra."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        first = governor.admit(_request("first", "remote_llm_light"), snapshot=_healthy_snapshot()).lease
        second = governor.admit(_request("second", "agent_cli"), snapshot=_healthy_snapshot()).lease
        # Ambas exceden su reserva; second (agent_cli) excede por más (1 GiB vs 0,5) y va primero.
        usage = {first.id: 1 * GIB, second.id: 2 * GIB}
        low_snapshot = _healthy_snapshot(available_memory_bytes=4 * GIB)
        base_time = "2026-01-01T00:00:00.000Z"

        first_round = governor.violations_for_snapshot(
            low_snapshot, usage_source=lambda: usage, now_iso=base_time
        )
        still_in_grace = governor.violations_for_snapshot(
            low_snapshot, usage_source=lambda: usage, now_iso=iso_after_seconds(base_time, 5)
        )
        after_grace = governor.violations_for_snapshot(
            low_snapshot, usage_source=lambda: usage, now_iso=iso_after_seconds(base_time, 6.5)
        )
        # Ninguna de las dos murió (sus leases siguen activas): pasada otra gracia, ninguna es
        # candidata de nuevo. Una víctima que no muere no bloquea a la otra, pero tampoco se repite.
        no_more_candidates = governor.violations_for_snapshot(
            low_snapshot, usage_source=lambda: usage, now_iso=iso_after_seconds(base_time, 13)
        )
        governor.release(second.id, reason="test_process_finally_exited")
        resolved_at_values = [
            row[0]
            for row in connection.execute(
                "SELECT resolved_at FROM resource_violations WHERE lease_id = ?", (second.id,)
            ).fetchall()
        ]

    assert [violation.execution_id for violation in first_round] == ["second"]
    assert still_in_grace == []
    assert [violation.execution_id for violation in after_grace] == ["first"]
    assert no_more_candidates == []
    assert resolved_at_values and all(value is not None for value in resolved_at_values)


def test_releasing_a_lease_resolves_its_violations_so_a_retry_is_not_cancelled_forever(
    tmp_path: Path,
) -> None:
    """Antes: la exclusión y `cancellation_reason` eran por ``execution_id`` y nunca se resolvían;
    un reintento con el mismo ``execution_id`` habría quedado cancelado para siempre."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        managed = ManagedProcessRepository(connection)
        lease = governor.admit(_request("retry-exec", "remote_llm_light"), snapshot=_healthy_snapshot()).lease

        governor.violations_for_snapshot(_healthy_snapshot(available_memory_bytes=4 * GIB))
        cancelled_while_active = managed.cancellation_reason("retry-exec")

        governor.release(lease.id, reason="test_process_finally_exited")
        cancelled_after_release = managed.cancellation_reason("retry-exec")

        retry = governor.admit(_request("retry-exec", "remote_llm_light"), snapshot=_healthy_snapshot())
        cancelled_for_the_retry = managed.cancellation_reason("retry-exec")

    assert cancelled_while_active is not None
    assert cancelled_after_release is None
    assert retry.status == "admitted"
    assert cancelled_for_the_retry is None


def test_hard_floor_treats_a_container_backed_lease_as_a_candidate(tmp_path: Path) -> None:
    """``live_memory_by_lease`` no mide contenedores (ver ADR-005): aparecen con uso 0, no ausentes."""
    from local_control_center.process_supervision.memory_usage import live_memory_by_lease

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        lease = governor.admit(_request("container-exec", "build_heavy"), snapshot=_healthy_snapshot()).lease
        connection.execute(
            """
            INSERT INTO managed_containers
                (name, execution_id, executable, owner_pid, owner_create_time, resource_lease_id,
                 owns_lease, created_at, released_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            ("aido-container-test", "container-exec", "docker", 1, 1.0, lease.id, 1, utc_now()),
        )

        violations = governor.violations_for_snapshot(
            _healthy_snapshot(available_memory_bytes=4 * GIB),
            usage_source=lambda: live_memory_by_lease(connection),
        )

    assert len(violations) == 1
    assert violations[0].lease_id == lease.id


def test_resource_wait_is_written_once_per_reason_and_reaches_the_thread(tmp_path: Path) -> None:
    """El governor reevalúa cada ~2 s: sólo un cambio de motivo es noticia para el job y el hilo."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="AIDO", path=tmp_path, template_id="other"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-capacity",
            title="Capacity wait",
            summary="",
        )
        job = JobsRepository(connection).create_job(
            project_id=project["id"], kind="thread.product_loop.run", payload={"threadId": thread["id"]}
        )["job"]
        governor = HostResourceGovernor(connection)
        for _ in range(5):
            governor.admit(
                _request(job["id"], "agent_cli", job_id=job["id"]),
                snapshot=_healthy_snapshot(available_memory_bytes=4 * GIB),
            )
        governor.admit(
            _request(job["id"], "agent_cli", job_id=job["id"]),
            snapshot=_healthy_snapshot(available_memory_bytes=12 * GIB),
        )
        job_events = [
            event
            for event in JobsRepository(connection).list_events(project["id"])
            if event["type"] == "job.resource_wait"
        ]
        thread_events = [
            event
            for event in ThreadsRepository(connection).list_events(thread["id"])
            if event["type"] == "resource_wait"
        ]

    assert Counter(event["payload"]["reasonCode"] for event in job_events) == {
        "hard_memory_floor": 1,
        "minimum_free_memory": 1,
    }
    assert [event["payload"]["reasonCode"] for event in thread_events] == [
        "hard_memory_floor",
        "minimum_free_memory",
    ]
    assert thread_events[0]["payload"]["jobId"] == job["id"]
    assert thread_events[0]["agentRole"] == "worker"


def test_local_model_call_is_a_light_client_that_still_yields_to_unreal(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        admitted = governor.admit(_request("local-one", "local_model_call"), snapshot=_healthy_snapshot())
        unreal = _healthy_snapshot(unreal_editor_running=True)
        blocked = governor.admit(_request("local-two", "local_model_call"), snapshot=unreal)
        conflict = governor.local_inference_conflict("local_model_call", snapshot=unreal)
        remote = governor.local_inference_conflict("remote_llm_light", snapshot=unreal)
    assert admitted.lease is not None
    assert (admitted.lease.gpu_required, admitted.lease.memory_limit_bytes) == (False, 2 * GIB)
    assert blocked.reason_code == "unreal_local_gpu_conflict"
    assert conflict is not None and conflict.reason_code == "unreal_local_gpu_conflict"
    assert remote is None


def test_admission_counts_the_measured_request_not_the_hard_cap(tmp_path: Path) -> None:
    """Un hilo reservaba 8 GiB aunque sus procesos midieron 170 MB (p50) y 2,99 GB (máximo).

    La admisión suma lo que la clase realmente usa (``memory_request_bytes``); el tope del Job
    Object sigue siendo ``memory_limit_bytes``, así que un proceso desbocado se corta igual.
    """
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        snapshot = _healthy_snapshot(available_memory_bytes=23 * GIB)
        cli = governor.admit(_request("cli", "agent_cli"), snapshot=snapshot)
        remote = governor.admit(_request("remote", "remote_llm_light"), snapshot=snapshot)

    assert cli.status == "admitted"
    assert cli.lease.memory_request_bytes == 1 * GIB
    assert cli.lease.memory_limit_bytes == 8 * GIB
    assert remote.status == "admitted"


def test_a_lease_from_before_the_split_still_counts_at_its_cap(tmp_path: Path) -> None:
    """Las leases vivas escritas antes de la columna no tienen request: cuentan por su tope."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        snapshot = _healthy_snapshot(available_memory_bytes=23 * GIB)
        lease = governor.admit(_request("legacy", "agent_cli"), snapshot=snapshot).lease
        connection.execute("UPDATE resource_leases SET memory_request_bytes = NULL WHERE id = ?", (lease.id,))
        refused = governor.admit(_request("qa", "qa_light"), snapshot=snapshot)

    assert (refused.status, refused.reason_code) == ("resource_wait", "aggregate_memory_budget")


def test_the_memory_refusal_states_the_numbers_it_compared(tmp_path: Path) -> None:
    """Ante un incidente de sobrecompromiso tiene que poder leerse qué request se usó."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        snapshot = _healthy_snapshot(available_memory_bytes=19 * GIB)
        refused = governor.admit(_request("qa", "qa_light"), snapshot=snapshot)

    assert refused.reason_code == "aggregate_memory_budget"
    assert "4.0 GiB requested" in refused.reason
    assert "3.0 GiB of headroom" in refused.reason


def test_every_request_fits_under_its_cap_and_unmeasured_classes_keep_the_cap() -> None:
    """Sin datos (o con uso bimodal) la reserva sigue siendo el tope: la regla del diseño."""
    from local_control_center.host_resources.profiles import WORKLOAD_PROFILES

    for profile in WORKLOAD_PROFILES.values():
        assert (
            profile.memory_request_bytes is None or profile.memory_request_bytes <= profile.memory_limit_bytes
        )
    for unmeasured in ("control_plane", "qa_light", "local_model_call", "browser_test", "local_gpu_model"):
        assert WORKLOAD_PROFILES[unmeasured].memory_request_bytes is None


def test_a_profile_cannot_reserve_more_memory_than_its_cap() -> None:
    """Un request sobre el tope reservaría memoria que el Job Object nunca deja usar: se rechaza al construir."""
    from pydantic import ValidationError

    from local_control_center.host_resources.models import WorkloadProfile
    from local_control_center.host_resources.profiles import WORKLOAD_PROFILES

    calibrated = WORKLOAD_PROFILES["agent_cli"].model_dump()
    with pytest.raises(ValidationError, match="memory_request_bytes"):
        WorkloadProfile.model_validate(
            {**calibrated, "memory_request_bytes": calibrated["memory_limit_bytes"] + 1}
        )
