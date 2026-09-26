"""Cola justa entre proyectos: tras el tier de reparación, gana el proyecto atendido hace más tiempo.

Un proyecto que encola muchos jobs no debe acaparar el worker mientras otro proyecto espera: dentro
del mismo tier de prioridad, `peek_next_job` alterna hacia el proyecto cuyo último `job_runs.started_at`
es más antiguo (o `NULL` si nunca fue atendido), conservando FIFO por `created_at, rowid` dentro de
cada proyecto.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _project(connection, tmp_path: Path, name: str) -> dict:
    return ProjectsRepository(connection).create_project(name=name, path=tmp_path / name, template_id="other")


def _thread_job(jobs: JobsRepository, project_id: str, thread_id: str) -> dict:
    return jobs.create_job(
        project_id=project_id, kind="thread.product_loop.run", payload={"threadId": thread_id}
    )["job"]


def _mark_control_plane_operation(connection, *, job_id: str, project_id: str) -> None:
    """Da a un job existente su fila `operational_executions` de tier 0 (`control_plane`)."""
    connection.execute(
        """
        INSERT INTO operational_executions
            (id, job_id, project_id, operation, workload_class, arguments_json, cwd, status, created_at)
        VALUES (?, ?, ?, 'noop', 'control_plane', '{}', '.', 'queued', '2026-01-01T00:00:00.000Z')
        """,
        (job_id, job_id, project_id),
    )


def test_a_project_flooding_the_queue_does_not_starve_a_later_project(tmp_path: Path) -> None:
    """A encola 5 jobs y el worker atiende el primero; B encola 1 job después: B sale antes que A#2."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_a = _project(connection, tmp_path, "Project A")
        project_b = _project(connection, tmp_path, "Project B")
        jobs = JobsRepository(connection)
        a_jobs = [_thread_job(jobs, project_a["id"], f"thread-a{i}") for i in range(5)]

        # Ninguno de los dos proyectos fue atendido todavía: FIFO global elige al primero de A.
        assert jobs.peek_next_job()["id"] == a_jobs[0]["id"]
        claimed = jobs.claim_next_job(worker_id="worker-1", job_id=a_jobs[0]["id"])
        assert claimed is not None

        b_job = _thread_job(jobs, project_b["id"], "thread-b0")

        # B nunca fue atendido; A sí (recién). B debe pasar antes que el resto de la cola de A.
        assert jobs.peek_next_job()["id"] == b_job["id"]

        jobs.claim_next_job(worker_id="worker-1", job_id=b_job["id"])

        # Atendido B, A retoma su propio FIFO: el segundo job de A, no un tercero fuera de orden.
        assert jobs.peek_next_job()["id"] == a_jobs[1]["id"]


def test_fifo_is_preserved_within_the_same_project(tmp_path: Path) -> None:
    """Sin otro proyecto de por medio, un único proyecto conserva su orden de llegada."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "Solo project")
        jobs = JobsRepository(connection)
        created = [_thread_job(jobs, project["id"], f"thread-{i}") for i in range(3)]

        for expected in created:
            next_up = jobs.peek_next_job()
            assert next_up["id"] == expected["id"]
            jobs.claim_next_job(worker_id="worker-1", job_id=next_up["id"])

        assert jobs.peek_next_job() is None


def test_tier_zero_control_plane_still_wins_over_project_fairness(tmp_path: Path) -> None:
    """Una reparación control_plane de un proyecto ya atendido gana igual a uno nunca atendido."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_a = _project(connection, tmp_path, "Attended project")
        project_b = _project(connection, tmp_path, "Never attended project")
        jobs = JobsRepository(connection)

        first = _thread_job(jobs, project_a["id"], "thread-a0")
        jobs.claim_next_job(worker_id="worker-1", job_id=first["id"])

        # B nunca fue atendido: sin tier 0, ganaría por sobre A. Con un repair control_plane en A,
        # el tier de reparación sigue mandando primero.
        _thread_job(jobs, project_b["id"], "thread-b0")
        repair = jobs.create_job(project_id=project_a["id"], kind="operation.execute", payload={})["job"]
        _mark_control_plane_operation(connection, job_id=repair["id"], project_id=project_a["id"])

        assert jobs.peek_next_job()["id"] == repair["id"]
