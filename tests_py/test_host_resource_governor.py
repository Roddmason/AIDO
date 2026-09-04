from __future__ import annotations

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
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        first = governor.admit(_request("build-one", "build_heavy"), snapshot=_healthy_snapshot())
        second = governor.admit(_request("cli-two", "agent_cli"), snapshot=_healthy_snapshot())

    assert first.status == "admitted"
    assert first.lease is not None
    assert second.status == "resource_wait"
    assert second.reason_code == "heavy_workload_capacity"


def test_two_remote_llm_workloads_coexist_but_third_waits(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        decisions = [
            governor.admit(_request(f"remote-{index}", "remote_llm_light"), snapshot=_healthy_snapshot())
            for index in range(1, 4)
        ]

    assert [decision.status for decision in decisions] == ["admitted", "admitted", "resource_wait"]
    assert decisions[-1].reason_code == "light_workload_capacity"


def test_unreal_and_heavy_conflicts_have_specific_reasons(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        light = governor.admit(_request("remote-one", "remote_llm_light"), snapshot=_healthy_snapshot())
        cook = governor.admit(_request("cook-one", "unreal_cook"), snapshot=_healthy_snapshot())

    assert light.status == "admitted"
    assert cook.status == "resource_wait"
    assert cook.reason_code == "unreal_cook_exclusive"


def test_expired_lease_is_recovered_and_capacity_is_reusable(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
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
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
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
    with open_sqlite_connection(db_path) as connection:
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
    with open_sqlite_connection(db_path) as connection:
        waiting_job = JobsRepository(connection).get_job(job["id"])

    monkeypatch.setattr(
        "local_control_center.jobs_approvals.worker.execute_job",
        lambda *_args, **_kwargs: {"summary": "completed", "metadata": {}},
    )
    completed = ConcurrentWorker(
        db_path=db_path,
        resource_snapshot=_healthy_snapshot(),
    ).run_once(worker_id="worker-one")
    with open_sqlite_connection(db_path) as connection:
        final_job = JobsRepository(connection).get_job(job["id"])
        active_leases = ResourceRepository(connection).active_leases()

    assert deferred is None
    assert waiting_job["status"] == "resource_wait"
    assert completed is not None
    assert final_job["status"] == "completed"
    assert active_leases == []


def test_hard_floor_records_cancellation_request_for_nonessential_workload(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        governor = HostResourceGovernor(connection)
        admitted = governor.admit(_request("remote-one", "remote_llm_light"), snapshot=_healthy_snapshot())
        violations = governor.violations_for_snapshot(_healthy_snapshot(available_memory_bytes=4 * GIB))

    assert admitted.status == "admitted"
    assert len(violations) == 1
    assert violations[0].action == "cancel_non_essential_workload"
    assert violations[0].execution_id == "remote-one"
