from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.jobs_approvals.repository import JobsRepository, StaleWorkerFenceError
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workers.leadership import WorkerControlRepository, WorkerLeadershipRepository


def test_worker_leadership_takeover_increments_fencing_token(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as first, open_sqlite_connection(db_path) as second:
        initialize_platform_schema(first)
        initialize_platform_schema(second)
        leader = WorkerLeadershipRepository(first).acquire(
            owner_id="worker-one",
            lease_seconds=5,
            now="2999-01-01T00:00:00.000Z",
        )
        standby = WorkerLeadershipRepository(second).acquire(
            owner_id="worker-two",
            lease_seconds=5,
            now="2999-01-01T00:00:02.000Z",
        )
        takeover = WorkerLeadershipRepository(second).acquire(
            owner_id="worker-two",
            lease_seconds=5,
            now="2999-01-01T00:00:06.000Z",
        )

    assert leader.acquired is True
    assert standby.acquired is False
    assert takeover.acquired is True
    assert takeover.fencing_token == leader.fencing_token + 1


def test_stale_leader_cannot_complete_job_after_takeover(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with open_sqlite_connection(db_path) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Fencing",
            path=tmp_path / "project",
            template_id="other",
        )
        job = JobsRepository(connection).create_job(
            project_id=project["id"],
            kind="prompt.optimize",
        )["job"]
        old_leader = WorkerLeadershipRepository(connection).acquire(
            owner_id="worker-old",
            lease_seconds=5,
            now="2999-01-01T00:00:00.000Z",
        )
        claimed = JobsRepository(connection).claim_next_job(
            worker_id="worker-old",
            leader_fencing_token=old_leader.fencing_token,
        )
        new_leader = WorkerLeadershipRepository(connection).acquire(
            owner_id="worker-new",
            lease_seconds=30,
            now="2999-01-01T00:00:06.000Z",
        )

        with pytest.raises(StaleWorkerFenceError):
            JobsRepository(connection).complete_job_run(
                job_id=job["id"],
                run_id=claimed["run"]["id"],
                status="completed",
                worker_id="worker-old",
                leader_fencing_token=old_leader.fencing_token,
            )

        persisted = JobsRepository(connection).get_job(job["id"])

    assert new_leader.fencing_token > old_leader.fencing_token
    assert persisted["status"] == "running"
    assert persisted["leaseOwner"] == "worker-old"


def test_worker_control_routes_only_write_durable_state(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)

    with TestClient(app) as client:
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token}
        resumed = client.post("/api/v1/workers/resume", headers=headers)
        run_once = client.post("/api/v1/workers/run-once", headers=headers)

    with open_sqlite_connection(runtime.db_path) as connection:
        initialize_platform_schema(connection)
        control = WorkerControlRepository(connection).get()
    runtime.close()

    assert resumed.status_code == 200
    assert resumed.json()["connected"] is False
    assert resumed.json()["desiredState"] == "running"
    assert run_once.status_code == 202
    assert control["desiredState"] == "running"
    assert control["runOnceRequestedAt"] is not None
    assert not hasattr(app.state, "worker_runtime")


def test_worker_leadership_migration_is_reentrant(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        versions = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 59"
        ).fetchone()
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'worker_%'"
            ).fetchall()
        }

    assert versions["total"] == 1
    assert {"worker_leader_leases", "worker_control_state", "worker_heartbeats"} <= tables
