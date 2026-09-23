"""Jobs hijos inline zombi: un hijo `running` sin lease se falla cuando su padre ya no corre.

Los agentes anidados (ProductOwner, Architect, DevOps, Developer, assessment) crean su job `running`
sin lease dentro del job del worker; ``requeue_expired_jobs`` exige lease, así que cuando el padre
pierde el suyo el hijo quedaba `running` para siempre (caso vivo: job-31a6b390, desde 2026-07-18).

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _project(connection: sqlite3.Connection, tmp_path: Path) -> dict:
    return ProjectsRepository(connection).create_project(
        name="Orphan children", path=tmp_path / "project", template_id="other"
    )


def _child_inside(jobs: JobsRepository, project_id: str, parent_id: str, db_path: Path) -> dict:
    with execution_scope(ProcessExecutionContext(db_path=db_path, execution_id=parent_id)):
        return jobs.create_job(
            project_id=project_id, kind="agent.product_owner", status="running", payload={"taskId": "child"}
        )["job"]


def _agent_run(connection: sqlite3.Connection, project_id: str, job_id: str) -> str:
    run_id = f"agent-run-{job_id}"
    connection.execute(
        """
        INSERT INTO agent_runs
            (id, project_id, job_id, workflow_run_id, workflow_step_id, status, input, output, metadata,
             created_at, updated_at)
        VALUES (?, ?, ?, NULL, NULL, 'running', '{}', '{}', '{}',
                '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """,
        (run_id, project_id, job_id),
    )
    return run_id


def _claimed_parent(jobs: JobsRepository, project_id: str) -> tuple[dict, dict]:
    parent = jobs.create_job(
        project_id=project_id, kind="thread.product_loop.run", payload={"threadId": "t"}
    )["job"]
    claimed = jobs.claim_next_job(worker_id="worker-1", job_id=parent["id"])
    return claimed["job"], claimed["run"]


def test_child_created_inside_a_job_records_its_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, _run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)
        outside = jobs.create_job(project_id=project["id"], kind="agent.architect", status="running")["job"]

    assert child["payload"]["parentJobId"] == parent["id"]
    assert "parentJobId" not in outside["payload"]


def test_execution_id_that_is_not_a_job_is_not_recorded_as_parent(tmp_path: Path) -> None:
    """``connection_execution_scope`` puede fijar un agent_run_id: estamparlo haría ver huérfano a un hijo vivo."""
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        child = _child_inside(jobs, project["id"], "agent-run-not-a-job", db_path)

        assert "parentJobId" not in child["payload"]
        assert jobs.fail_orphaned_child_jobs() == []
        assert jobs.get_job(child["id"])["status"] == "running"


def test_parent_lease_expiry_fails_its_running_child_and_agent_run(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, _run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)
        run_id = _agent_run(connection, project["id"], child["id"])
        connection.execute(
            "UPDATE jobs SET lease_expires_at = '2026-01-01T00:00:00.000Z' WHERE id = ?", (parent["id"],)
        )

        jobs.requeue_expired_jobs(now_iso="2026-01-01T00:05:00.000Z")
        child_after = jobs.get_job(child["id"])
        run_status = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()[0]
        parent_after = jobs.get_job(parent["id"])

    assert parent_after["status"] == "queued"
    assert child_after["status"] == "failed"
    assert child_after["payload"]["result"]["reason"] == "parent_lease_expired"
    assert run_status == "failed"


def test_completing_the_parent_fails_a_child_left_running(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)

        jobs.complete_job_run(job_id=parent["id"], run_id=run["id"], status="completed", summary="done")

        assert jobs.get_job(child["id"])["status"] == "failed"


def test_child_of_a_running_parent_is_left_alone(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, _run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)

        assert jobs.fail_orphaned_child_jobs() == []
        assert jobs.get_job(child["id"])["status"] == "running"


def test_child_without_a_provable_parent_is_never_reaped_by_age(tmp_path: Path) -> None:
    """Sin ``parentJobId`` no hay prueba de que el padre murió: la antigüedad sola no basta."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        old = jobs.create_job(
            project_id=project["id"],
            kind="agent.product_owner",
            status="running",
            workflow_run_id="product-loop-legacy",
        )["job"]
        connection.execute(
            "UPDATE jobs SET updated_at = '2026-01-01T00:00:00.000Z' WHERE id = ?", (old["id"],)
        )

        reaped = jobs.fail_orphaned_child_jobs(now_iso="2027-01-01T00:00:00.000Z")
        jobs.requeue_expired_jobs(now_iso="2027-01-01T00:00:00.000Z")

        assert reaped == []
        assert jobs.get_job(old["id"])["status"] == "running"


def test_legacy_cleanup_fails_only_validated_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        legacy = jobs.create_job(project_id=project["id"], kind="agent.product_owner", status="running")[
            "job"
        ]
        run_id = _agent_run(connection, project["id"], legacy["id"])
        parent, _run = _claimed_parent(jobs, project["id"])
        linked = _child_inside(jobs, project["id"], parent["id"], db_path)
        finished = jobs.create_job(project_id=project["id"], kind="agent.developer", status="completed")[
            "job"
        ]

        reaped = jobs.fail_legacy_orphan_child_jobs(
            [legacy["id"], linked["id"], finished["id"], parent["id"], "job-missing"],
            now_iso="2026-01-01T00:05:00.000Z",
        )
        run_status = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()[0]

        assert reaped == [legacy["id"]]
        assert jobs.get_job(legacy["id"])["payload"]["result"]["reason"] == "parent_lease_expired"
        assert run_status == "failed"
        assert jobs.get_job(linked["id"])["status"] == "running"
        assert jobs.get_job(finished["id"])["status"] == "completed"
        assert jobs.get_job(parent["id"])["status"] == "running"
