"""Tests del control anti-loop concurrente en threads: nota del operador, guard de ejecución activa,
cancelación de la ejecución en curso y creación de un hilo nuevo mientras otro ejecuta.

Cubren las reglas del control: un hilo en ejecución no acepta un post normal (evita loops
concurrentes accidentales), la nota se registra como ``operator_note`` sin encolar job, cancelar
detiene los jobs en vuelo y reabre el hilo, y crear un hilo nuevo desde uno en ejecución funciona.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.jobs_approvals.worker import (
    _execute_thread_product_loop_job,
    _finish_thread_after_product_loop,
)
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads.repository import ThreadsRepository


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Threads notes", path=tmp_path / "threads", template_id="other"
    )
    return project["id"]


def _headers(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def _create_thread(client, headers, project_id: str) -> dict:
    response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project_id,
            "ownerType": "workspace",
            "ownerId": "workspace-1",
            "title": "Anti concurrent loop thread",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["thread"]


_CSV_EXPORT_GOAL = "Implement the CSV export button on the reports page and wire it to the API."
_RATE_LIMITER_GOAL = "Implement a rate limiter on the login endpoint to block brute force attempts."


def _queue_run(client, headers, thread_id: str, content: str = _CSV_EXPORT_GOAL) -> dict:
    """Publica un mensaje claro y accionable que el coordinator encola como product-loop run."""
    response = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"content": content},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["thread"]["status"] == "queued", body
    return body


def test_operator_note_is_recorded_as_operator_note_without_queuing_a_run(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    _queue_run(client, headers, thread["id"])
    jobs_before = len(JobsRepository(runtime.connection).list_jobs(project_id=project_id))

    note = client.post(
        f"/api/v1/threads/{thread['id']}/notes",
        headers=headers,
        json={"content": "Reminder: keep the button aligned with the toolbar, don't add a new row."},
    )

    assert note.status_code == 200, note.text
    payload = note.json()
    # The note is registered with the dedicated operator_note kind, not a normal user message.
    assert payload["message"]["kind"] == "operator_note"
    assert payload["message"]["author"] == "operator"
    # Adding a note must not start a second run: status is untouched and no new job was queued.
    assert payload["thread"]["status"] == "queued"
    jobs_after = len(JobsRepository(runtime.connection).list_jobs(project_id=project_id))
    assert jobs_after == jobs_before

    detail = client.get(f"/api/v1/threads/{thread['id']}").json()
    assert any(message["kind"] == "operator_note" for message in detail["messages"])
    assert any(event["type"] == "operator_note_added" for event in detail["events"])
    runtime.close()


def test_running_thread_rejects_a_normal_message_post(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    ThreadsRepository(runtime.connection).set_status(thread["id"], "running")
    jobs_before = len(JobsRepository(runtime.connection).list_jobs(project_id=project_id))

    response = client.post(
        f"/api/v1/threads/{thread['id']}/messages",
        headers=headers,
        json={"content": "Also refactor the whole reports module while you're at it."},
    )

    # A running thread does not accept a normal post; it fails closed with a conflict instead of
    # silently spawning a concurrent product-loop run.
    assert response.status_code == 409, response.text
    assert "running" in response.json()["detail"]
    jobs_after = len(JobsRepository(runtime.connection).list_jobs(project_id=project_id))
    assert jobs_after == jobs_before
    runtime.close()


def test_cancel_execution_stops_the_job_and_reopens_the_thread(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    run = _queue_run(client, headers, thread["id"])
    job_id = run["run"]["jobId"]
    assert job_id

    cancelled = client.post(
        f"/api/v1/threads/{thread['id']}/cancel",
        headers=headers,
        json={"reason": "Wrong goal, stopping."},
    )

    assert cancelled.status_code == 200, cancelled.text
    payload = cancelled.json()
    assert job_id in payload["cancelledJobIds"]
    assert payload["thread"]["status"] == "open"
    assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "cancelled"

    detail = client.get(f"/api/v1/threads/{thread['id']}").json()
    assert any(event["type"] == "execution_cancelled" for event in detail["events"])

    # Once cancelled the thread is idle again, so the "cancel and replace" flow can post a fresh run.
    replaced = client.post(
        f"/api/v1/threads/{thread['id']}/messages",
        headers=headers,
        json={"content": "Instead, add pagination controls to the reports table."},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["thread"]["status"] == "queued"
    runtime.close()


def test_creating_a_new_thread_while_another_runs_works(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    running = _create_thread(client, headers, project_id)
    _queue_run(client, headers, running["id"])
    ThreadsRepository(runtime.connection).set_status(running["id"], "running")

    created = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project_id,
            "ownerType": "workspace",
            "ownerId": "workspace-1",
            "title": "Second objective in parallel thread",
        },
    )
    assert created.status_code == 201, created.text
    new_thread_id = created.json()["thread"]["id"]

    # Distinct, low-overlap goal so the similarity gate does not confuse it with the running thread.
    started = _queue_run(client, headers, new_thread_id, content=_RATE_LIMITER_GOAL)

    # Starting a brand-new thread never touches the running one: the new thread queues its own run
    # while the original keeps executing.
    assert started["thread"]["status"] == "queued"
    assert ThreadsRepository(runtime.connection).get_thread(running["id"])["status"] == "running"
    runtime.close()


def test_cancelled_running_job_does_not_resurrect_the_thread(tmp_path: Path) -> None:
    """The dangerous case behind the feature: a worker already executing a job must not overwrite the
    thread status after the operator stopped it — otherwise "Stop execution" is a lie and the
    cancel-and-replace flow could run two loops at once."""
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    run = _queue_run(client, headers, thread["id"])
    job_id = run["run"]["jobId"]
    connection = runtime.connection

    # A worker claims the job and marks the thread running, exactly as it does before executing.
    claimed = JobsRepository(connection).claim_next_job(worker_id="worker-test")
    assert claimed is not None and claimed["job"]["id"] == job_id
    ThreadsRepository(connection).set_status(thread["id"], "running")

    # Operator stops the execution mid-run: the running job is cancelled and the thread reopens.
    cancelled = client.post(f"/api/v1/threads/{thread['id']}/cancel", headers=headers, json={})
    assert cancelled.status_code == 200, cancelled.text
    assert job_id in cancelled.json()["cancelledJobIds"]
    assert cancelled.json()["thread"]["status"] == "open"

    # The worker eventually finishes the now-cancelled job; the finalizer must abort, not resurrect.
    _finish_thread_after_product_loop(
        threads=ThreadsRepository(connection),
        thread_id=thread["id"],
        job_id=job_id,
        status="blocked",
        reason="Product loop ended after the operator cancelled it.",
        loop_id=None,
        evidence_id=None,
    )

    assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "open"
    detail = client.get(f"/api/v1/threads/{thread['id']}").json()
    assert any(event["type"] == "worker_aborted" for event in detail["events"])
    # The misleading terminal "blocked" message from the cancelled run was never appended.
    assert not any(
        message["kind"] == "error" and "ended after the operator cancelled" in message["content"]
        for message in detail["messages"]
    )
    runtime.close()


def test_uncancelled_job_still_finalizes_the_thread_normally(tmp_path: Path) -> None:
    """Negative control: the cancellation guard must not over-fire — a job that was NOT cancelled must
    still finalize the thread to its terminal status when the worker finishes."""
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    run = _queue_run(client, headers, thread["id"])
    job_id = run["run"]["jobId"]
    connection = runtime.connection

    JobsRepository(connection).claim_next_job(worker_id="worker-test")
    ThreadsRepository(connection).set_status(thread["id"], "running")

    _finish_thread_after_product_loop(
        threads=ThreadsRepository(connection),
        thread_id=thread["id"],
        job_id=job_id,
        status="blocked",
        reason="Runtime unavailable.",
        loop_id=None,
        evidence_id=None,
    )

    assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "blocked"
    runtime.close()


def test_note_and_cancel_return_404_for_an_unknown_thread(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    _project(runtime, tmp_path)

    note = client.post("/api/v1/threads/thread-missing/notes", headers=headers, json={"content": "hi"})
    assert note.status_code == 404, note.text

    cancel = client.post("/api/v1/threads/thread-missing/cancel", headers=headers, json={})
    assert cancel.status_code == 404, cancel.text
    runtime.close()


class _GitServiceNeverUsed:
    """Git double que falla si el loop llega a tocarlo: prueba que el abort ocurrió antes del trabajo real."""

    def status(self, project_id: str) -> dict:
        raise AssertionError("A cancelled Product Loop must abort before checking the git workspace.")


def test_cancelled_product_loop_aborts_before_doing_any_stage_work(tmp_path: Path) -> None:
    """Cancelar debe detener la ejecución en vuelo, no solo marcar la fila del job.

    Sin el abort cooperativo el worker ya reclamado sigue corriendo el loop completo mientras el run de
    reemplazo arranca: dos product loops concurrentes sobre el mismo hilo.
    """
    runtime, _client_unused = _client(tmp_path)
    project_id = _project(runtime, tmp_path)

    result = ProductLoopCoordinator(runtime.connection, root=tmp_path).run_user_message(
        project_id=project_id,
        message=_CSV_EXPORT_GOAL,
        root=tmp_path,
        should_abort=lambda: True,
        git_service=_GitServiceNeverUsed(),
    )

    assert result["status"] == "cancelled"
    assert result["loop"]["state"] == "cancelled"
    # Aborted at the very first stage boundary: the run never advanced past its initial state.
    states = [transition["toState"] for transition in result["transitions"]]
    assert states == ["goal_received", "cancelled"], states
    runtime.close()


def test_product_loop_abort_fires_at_the_next_stage_boundary_not_earlier(tmp_path: Path) -> None:
    """Control positivo y negativo a la vez: el guard no se dispara antes de tiempo (el loop avanza a
    ``workspace_check``) y sí corta en el siguiente límite de etapa cuando el operador cancela."""
    runtime, _client_unused = _client(tmp_path)
    project_id = _project(runtime, tmp_path)
    checks: list[int] = []

    def should_abort() -> bool:
        checks.append(1)
        return len(checks) > 1  # the operator cancels while the loop sits in workspace_check

    result = ProductLoopCoordinator(runtime.connection, root=tmp_path).run_user_message(
        project_id=project_id,
        message=_CSV_EXPORT_GOAL,
        root=tmp_path,
        should_abort=should_abort,
        git_service=_GitServiceNeverUsed(),
    )

    assert result["status"] == "cancelled"
    # It advanced one real stage before the cancel landed, then stopped at the next boundary.
    states = [transition["toState"] for transition in result["transitions"]]
    assert states == ["goal_received", "workspace_check", "cancelled"], states
    runtime.close()


def test_worker_never_starts_the_product_loop_for_an_already_cancelled_job(
    tmp_path: Path, monkeypatch
) -> None:
    """Cierra la ventana reclamar→cancelar: si el job ya fue cancelado, el worker no debe arrancar el loop."""
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    _queue_run(client, headers, thread["id"])
    connection = runtime.connection

    claimed = JobsRepository(connection).claim_next_job(worker_id="worker-test")
    assert claimed is not None
    cancelled = client.post(f"/api/v1/threads/{thread['id']}/cancel", headers=headers, json={})
    assert cancelled.status_code == 200, cancelled.text

    def _explode(*args, **kwargs):
        raise AssertionError("The worker must not run the Product Loop for a cancelled job.")

    monkeypatch.setattr("local_control_center.jobs_approvals.worker.ProductLoopCoordinator", _explode)
    execution = _execute_thread_product_loop_job(
        claimed["job"], connection=connection, worker_id="worker-test"
    )

    assert execution["metadata"]["productLoopStatus"] == "cancelled"
    assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "open"
    detail = client.get(f"/api/v1/threads/{thread['id']}").json()
    assert any(event["type"] == "worker_aborted" for event in detail["events"])
    runtime.close()


def test_completing_a_run_never_resurrects_a_cancelled_job(tmp_path: Path) -> None:
    """Un worker que termina tarde no puede reescribir el estado terminal `cancelled` del job: de lo
    contrario la auditoría miente y el guard de cancelación deja de ver el cancel."""
    runtime, client = _client(tmp_path)
    headers = _headers(runtime)
    project_id = _project(runtime, tmp_path)
    thread = _create_thread(client, headers, project_id)
    run = _queue_run(client, headers, thread["id"])
    job_id = run["run"]["jobId"]
    jobs = JobsRepository(runtime.connection)

    claimed = jobs.claim_next_job(worker_id="worker-test")
    assert claimed is not None
    jobs.cancel_job(job_id, reason="Operator stopped the execution.")

    jobs.complete_job_run(
        job_id=job_id,
        run_id=claimed["run"]["id"],
        status="completed",
        summary="Late finish from the already-cancelled worker.",
    )

    assert jobs.get_job(job_id)["status"] == "cancelled"
    runtime.close()
