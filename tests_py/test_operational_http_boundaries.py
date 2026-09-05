"""Regresiones de controles operacionales que no deben ejecutar trabajo en la API.

@author Rodrigo Mason
"""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from local_control_center.api import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.workers.leadership import WorkerControlRepository
from tests_py.test_remediation_blocker_experience import _project_and_thread


def test_overview_event_snapshot_does_not_run_migrations_during_get(tmp_path, monkeypatch):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        app = create_app(runtime=runtime, static_dir=None)

        def forbidden(*args, **kwargs):
            raise AssertionError("GET must not bootstrap migrations")

        monkeypatch.setattr("local_control_center.api.initialize_platform_schema", forbidden, raising=False)
        with TestClient(app) as client:
            assert client.get("/api/v1/events").status_code == 200
    finally:
        runtime.close()


def test_worker_remediation_requests_a_durable_batch_without_an_inprocess_worker(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        app = create_app(runtime=runtime, static_dir=None)
        _, thread = _project_and_thread(runtime.connection, tmp_path, "worker-durable")
        from local_control_center.threads.repository import ThreadsRepository

        ThreadsRepository(runtime.connection).set_status(thread["id"], "queued")
        with TestClient(app) as client:
            actions = client.get(f"/api/v1/threads/{thread['id']}/remediations").json()["remediations"]
            action = next(item for item in actions if item["actionType"] == "run_worker_once")
            response = client.post(
                f"/api/v1/remediations/{action['id']}/execute",
                headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
                json={},
            )
            assert response.status_code == 200
            assert response.json()["execution"]["status"] == "queued"
            assert WorkerControlRepository(runtime.connection).get()["runOnceRequestedAt"]
            assert runtime.connection.execute("SELECT COUNT(*) FROM managed_processes").fetchone()[0] == 0
    finally:
        runtime.close()


def test_secret_mutations_never_wait_for_vault_on_the_asgi_event_loop(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        app = create_app(runtime=runtime, static_dir=None)
        handlers = {
            route.endpoint.__name__: route.endpoint for route in app.routes if hasattr(route, "endpoint")
        }
        for name in ("create_credential", "rotate_credential", "delete_credential", "validate_credential"):
            assert not inspect.iscoroutinefunction(handlers[name]), name
    finally:
        runtime.close()


def test_heavy_remediation_is_queued_and_does_not_execute_git_in_http(tmp_path, monkeypatch):
    from local_control_center.remediations.service import BlockerRemediationService

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        app = create_app(runtime=runtime, static_dir=None)
        project, thread = _project_and_thread(runtime.connection, tmp_path, "git-queued")
        service = BlockerRemediationService(runtime.connection, root=tmp_path)
        action = service.repository.create_action(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="",
            stage="git",
            blocker_type="git_not_initialized",
            title="Initialize",
            description="Initialize repository",
            action_type="git_init",
            technical_reason="missing",
            primary=True,
            destructive=False,
            confirmation_required=False,
            payload={"projectId": project["id"]},
        )

        def forbidden(*args, **kwargs):
            raise AssertionError("Remediation must not execute in the HTTP process")

        monkeypatch.setattr(BlockerRemediationService, "execute", forbidden)
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/remediations/{action['id']}/execute",
                headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
                json={},
            )
            assert response.status_code == 202
            execution = client.get(f"/api/v1/executions/{response.json()['executionId']}").json()
            assert execution["status"] == "queued"
            assert execution["projectId"] == project["id"]
    finally:
        runtime.close()


def test_runtime_revalidation_is_light_and_precedes_the_conversation_it_repairs(tmp_path):
    from local_control_center.jobs_approvals.repository import JobsRepository
    from local_control_center.remediations.service import BlockerRemediationService

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        app = create_app(runtime=runtime, static_dir=None)
        project, thread = _project_and_thread(runtime.connection, tmp_path, "runtime-repair")
        jobs = JobsRepository(runtime.connection)
        conversation = jobs.create_job(
            project_id=project["id"], kind="thread.product_loop.run", payload={"threadId": thread["id"]}
        )["job"]
        service = BlockerRemediationService(runtime.connection, root=tmp_path)
        actions = {}
        for kind in ("validate_runtime", "git_init"):
            actions[kind] = service.repository.create_action(
                project_id=project["id"],
                thread_id=thread["id"],
                loop_id="",
                stage="runtime",
                blocker_type="runtime_not_executable",
                title=kind,
                description=kind,
                action_type=kind,
                technical_reason="missing",
                primary=True,
                destructive=False,
                confirmation_required=False,
                payload={"projectId": project["id"]},
            )
        with TestClient(app) as client:
            for kind, action in actions.items():
                response = client.post(
                    f"/api/v1/remediations/{action['id']}/execute",
                    headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
                    # Client claims must not downgrade the persisted action's resource class.
                    json={"payload": {"actionType": "validate_runtime", "workloadClass": "qa_light"}},
                )
                assert response.status_code == 202
                execution = client.get(f"/api/v1/executions/{response.json()['executionId']}").json()
                assert execution["workloadClass"] == (
                    "qa_light" if kind == "validate_runtime" else "agent_cli"
                )
                if kind == "validate_runtime":
                    assert jobs.peek_next_job()["id"] == execution["executionId"]
        assert jobs.get_job(conversation["id"])["status"] == "queued"
    finally:
        runtime.close()
