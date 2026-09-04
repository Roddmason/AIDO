from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from local_control_center.api import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime


class SlowInput(BaseModel):
    projectId: str
    message: str


class ValidatedResult(BaseModel):
    count: int


def test_openapi_retains_terminal_contract_separately_from_202(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    try:
        app = create_app(runtime=runtime, static_dir=None)
        schema = app.openapi()
        operation = schema["paths"]["/api/v1/model-gateway/route/execute"]["post"]
        assert "200" not in operation["responses"]
        assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ExecutionAccepted"
        }
        contract = operation["x-aido-execution"]
        assert contract["resultSchema"] == {"$ref": "#/components/schemas/RouteExecuteResponse"}
        assert "RouteExecuteResponse" in schema["components"]["schemas"]
        assert app.openapi() == schema
    finally:
        runtime.close()


def test_worker_keeps_result_validation_without_exposing_invalid_payload(tmp_path):
    from local_control_center.executions.router import ExecutionRouter, queued_operation
    from tests_py.execution_client import CompletedExecutionClient

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    router = ExecutionRouter(platform=runtime, require_write=lambda request: None)

    @router.post("/test-typed-result", response_model=ValidatedResult)
    @queued_operation("test.typed_result", workload_class="qa_light")
    async def invalid_result(request: Request):
        return {"count": "PRIVATE_INVALID_PAYLOAD"}

    app.include_router(router)
    with CompletedExecutionClient(app) as client:
        response = client.post("/test-typed-result")
        assert response.status_code == 500
        assert "PRIVATE_INVALID_PAYLOAD" not in response.text
        execution = client.get(f"/api/v1/executions/{response.headers['X-Test-Execution-Id']}").json()
        assert execution["status"] == "failed"
    runtime.close()


@pytest.mark.parametrize("kind", ["thread.product_loop.run", "thread.research.run"])
def test_legacy_jobs_are_os_contained_and_retry_resets_operational_state(tmp_path, kind):
    import os

    from local_control_center.host_resources.models import ResourceSnapshot
    from local_control_center.jobs_approvals.repository import JobsRepository
    from local_control_center.jobs_approvals.worker import ConcurrentWorker
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        jobs = JobsRepository(runtime.connection)
        job = jobs.create_job(project_id="test", kind=kind, payload={})["job"]
        leader = WorkerLeadershipRepository(runtime.connection).acquire(
            owner_id="contained-test", lease_seconds=60
        )
        result = ConcurrentWorker(
            db_path=runtime.db_path, resource_snapshot=ResourceSnapshot.test_snapshot()
        ).run_once(worker_id="contained-test", fencing_token=leader.fencing_token)
        assert result["run"]["status"] == "failed"
        row = runtime.connection.execute(
            "SELECT * FROM managed_processes WHERE execution_id=?", (job["id"],)
        ).fetchone()
        assert row["root_pid"] != os.getpid()
        assert row["finished_at"] is not None
        assert (
            runtime.connection.execute(
                "SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL"
            ).fetchone()[0]
            == 0
        )
        jobs.retry_job(job["id"], reason="Test explicit retry")
        execution = runtime.connection.execute(
            "SELECT status, result_json FROM operational_executions WHERE id=?", (job["id"],)
        ).fetchone()
        assert tuple(execution) == ("queued", None)
    finally:
        runtime.close()


def test_cli_inventory_get_does_not_probe_executables(tmp_path, monkeypatch):
    from local_control_center.agents.runtime_registry import RuntimeRegistry

    def unexpected(*args, **kwargs):
        raise AssertionError("GET inventory must use durable evidence")

    monkeypatch.setattr(RuntimeRegistry, "list_runtimes", unexpected)
    monkeypatch.setattr(RuntimeRegistry, "detect", unexpected)
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
        response = client.get("/api/v1/model-gateway/cli-runtimes")
        assert response.status_code == 200
        assert any(row["runtime"] == "codex_cli" for row in response.json()["cliRuntimes"])
    runtime.close()


def test_git_gets_only_read_durable_snapshots(tmp_path, monkeypatch):
    from local_control_center.git_workspace.service import GitWorkspaceService

    def unexpected(*args, **kwargs):
        raise AssertionError("HTTP GET must not execute Git")

    monkeypatch.setattr(GitWorkspaceService, "status", unexpected)
    monkeypatch.setattr(GitWorkspaceService, "prepare_status", unexpected)
    monkeypatch.setattr(GitWorkspaceService, "diff", unexpected)
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
        project_id = runtime.ensure_runtime_project()["id"]
        for suffix in ("status", "branches", "diff"):
            response = client.get(f"/api/v1/projects/{project_id}/git/{suffix}")
            assert response.status_code == 200
            assert response.json()["refreshRequired"] is True
            assert response.json()["status"] != "completed"
        response = client.post(
            f"/api/v1/projects/{project_id}/git/refresh",
            headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
        )
        assert response.status_code == 202
    runtime.close()


@pytest.mark.parametrize(
    "status,expected", [("running", "failed"), ("completed", "completed"), ("cancelled", "cancelled")]
)
def test_expired_execution_is_not_replayed_and_preserves_terminal_outcome(tmp_path, status, expected):
    from local_control_center.executions.repository import ExecutionRepository
    from local_control_center.jobs_approvals.repository import JobsRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        repository = ExecutionRepository(runtime.connection)
        execution = repository.enqueue(
            operation="test.recovery",
            workload_class="qa_light",
            arguments={},
            project_id=None,
            cwd=str(tmp_path),
            result_status_code=200,
        )
        jobs = JobsRepository(runtime.connection)
        jobs.claim_next_job(worker_id="lost", job_id=execution["jobId"])
        runtime.connection.execute(
            "UPDATE operational_executions SET status=? WHERE id=?", (status, execution["jobId"])
        )
        runtime.connection.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00Z'")
        jobs.requeue_expired_jobs()
        assert jobs.get_job(execution["jobId"])["status"] == expected
        assert (
            runtime.connection.execute(
                "SELECT status FROM job_runs WHERE job_id=?", (execution["jobId"],)
            ).fetchone()[0]
            == expected
        )
        assert repository.get(execution["jobId"])["status"] == (
            "interrupted" if status == "running" else status
        )
    finally:
        runtime.close()


def test_enqueue_without_secure_storage_fails_closed(tmp_path, monkeypatch):
    from local_control_center.credentials.backends import CredentialBackendError
    from local_control_center.executions.inputs import OperationInputStore

    def unavailable(*args, **kwargs):
        raise CredentialBackendError("secure store unavailable")

    monkeypatch.setattr(OperationInputStore, "put", unavailable)
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    with TestClient(create_app(runtime=runtime, static_dir=None)) as client:
        response = client.post(
            "/api/v1/model-gateway/providers/missing/discover-models",
            headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
        )
        assert response.status_code == 503
        assert "configuration_required" in response.json()["detail"]
        assert runtime.connection.execute("SELECT COUNT(*) FROM operational_executions").fetchone()[0] == 0
    runtime.close()


def test_post_is_durable_202_and_does_not_execute_on_http_thread(tmp_path: Path):
    from local_control_center.executions.router import ExecutionRouter, queued_operation

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    called = threading.Event()

    def require_write(request: Request):
        if request.headers.get("X-Local-Control-Token") != runtime.get_handshake()["token"]:
            raise HTTPException(403)

    router = ExecutionRouter(platform=runtime, require_write=require_write)

    @router.post("/api/v1/_test/slow")
    @queued_operation("test.slow", workload_class="remote_llm_light")
    def slow(body: SlowInput, request: Request):
        require_write(request)
        called.set()
        time.sleep(1)
        return {"message": body.message}

    app.include_router(router)
    with TestClient(app) as client:
        body = {"projectId": runtime.ensure_runtime_project()["id"], "message": "hello"}
        assert client.post("/api/v1/_test/slow", json=body).status_code == 403
        started = time.monotonic()
        response = client.post(
            "/api/v1/_test/slow",
            json=body,
            headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
        )
        assert response.status_code == 202
        assert time.monotonic() - started < 0.5
        assert not called.is_set()
        execution_id = response.json()["executionId"]
        stored = runtime.connection.execute(
            "SELECT arguments_json FROM operational_executions WHERE id=?", (execution_id,)
        ).fetchone()[0]
        assert '"message"' not in stored
        assert "sealedInput" in stored
        state = client.get(f"/api/v1/executions/{execution_id}").json()
        assert state["status"] == "queued"
        assert state["operation"] == "test.slow"
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/overview").status_code == 200
        events = client.get(f"/api/v1/executions/{execution_id}/events").json()["events"]
        assert events[-1]["type"] == "execution.queued"
        cancel = client.post(
            f"/api/v1/executions/{execution_id}/cancel",
            json={"reason": "operator stop"},
            headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
        )
        assert cancel.status_code == 202
        assert cancel.json()["status"] == "cancelled"
    runtime.close()


def test_execution_finish_rejects_a_stale_worker_and_migration_is_reentrant(tmp_path):
    import pytest

    from local_control_center.executions.repository import ExecutionRepository
    from local_control_center.jobs_approvals.repository import JobsRepository, StaleWorkerFenceError
    from local_control_center.shared.migrations import initialize_platform_schema
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        connection = runtime.connection
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        repository = ExecutionRepository(connection)
        execution = repository.enqueue(
            operation="test.fence",
            workload_class="remote_llm_light",
            arguments={"sealedInput": "not-opened"},
            project_id=None,
            cwd=str(tmp_path),
            result_status_code=200,
        )
        leader = WorkerLeadershipRepository(connection).acquire(owner_id="old", lease_seconds=60)
        JobsRepository(connection).claim_next_job(
            worker_id="old",
            lease_ms=60000,
            leader_fencing_token=leader.fencing_token,
            job_id=execution["jobId"],
        )
        assert repository.start(execution["executionId"], owner_id="old", fencing_token=leader.fencing_token)
        connection.execute("UPDATE worker_leader_leases SET expires_at='2000-01-01T00:00:00Z'")
        successor = WorkerLeadershipRepository(connection).acquire(owner_id="new", lease_seconds=60)
        assert successor.fencing_token > leader.fencing_token
        with pytest.raises(StaleWorkerFenceError):
            repository.finish(
                execution["executionId"],
                owner_id="old",
                fencing_token=leader.fencing_token,
                status="completed",
                result={"ok": True},
            )
        assert repository.get(execution["executionId"])["result"] is None
    finally:
        runtime.close()


def test_worker_executes_queued_operation_in_contained_child(tmp_path):
    from local_control_center.host_resources.models import ResourceSnapshot
    from local_control_center.jobs_approvals.worker import ConcurrentWorker
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/model-gateway/providers/not-configured/discover-models",
            headers={"X-Local-Control-Token": runtime.get_handshake()["token"]},
        )
        assert response.status_code == 202
        execution_id = response.json()["executionId"]
        leader = WorkerLeadershipRepository(runtime.connection).acquire(
            owner_id="test-worker", lease_seconds=60
        )
        result = ConcurrentWorker(
            db_path=runtime.db_path, resource_snapshot=ResourceSnapshot.test_snapshot()
        ).run_once(worker_id="test-worker", fencing_token=leader.fencing_token)
        assert result["run"]["status"] == "failed"
        state = client.get(f"/api/v1/executions/{execution_id}").json()
        assert state["status"] == "blocked", state
        assert state["resultStatusCode"] == 404
        processes = runtime.connection.execute(
            "SELECT * FROM managed_processes WHERE execution_id=?", (execution_id,)
        ).fetchall()
        assert len(processes) == 1
        assert processes[0]["finished_at"] and processes[0]["root_pid"] > 0
    runtime.close()


def test_slow_injected_provider_does_not_block_http_reads_or_cancel(tmp_path):
    from local_control_center.executions.router import ExecutionRouter, queued_operation
    from local_control_center.executions.runner import run_registered_operation
    from local_control_center.host_resources.governor import HostResourceGovernor
    from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
    from local_control_center.jobs_approvals.repository import JobsRepository
    from local_control_center.workers.leadership import WorkerLeadershipRepository

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    entered, release = threading.Event(), threading.Event()

    class FakeProvider:
        def complete(self, message):
            entered.set()
            assert release.wait(timeout=10)
            return {"text": message}

    provider = FakeProvider()

    def auth(request):
        if request.headers.get("X-Local-Control-Token") != runtime.get_handshake()["token"]:
            raise HTTPException(403)

    router = ExecutionRouter(platform=runtime, require_write=auth)

    @router.post("/api/v1/_test/provider")
    @queued_operation("test.provider", workload_class="remote_llm_light")
    def run_provider(body: SlowInput, request: Request):
        auth(request)
        return provider.complete(body.message)

    app.include_router(router)
    results = []
    with TestClient(app) as client:
        headers = {"X-Local-Control-Token": runtime.get_handshake()["token"]}
        accepted = client.post(
            "/api/v1/_test/provider", json={"projectId": "test-project", "message": "test"}, headers=headers
        ).json()
        execution_id = accepted["executionId"]
        leader = WorkerLeadershipRepository(runtime.connection).acquire(
            owner_id="fake-provider-worker", lease_seconds=60
        )
        JobsRepository(runtime.connection).claim_next_job(
            worker_id="fake-provider-worker",
            lease_ms=60000,
            leader_fencing_token=leader.fencing_token,
            job_id=execution_id,
        )
        HostResourceGovernor(runtime.connection).admit(
            ResourceAdmissionRequest(
                execution_id=execution_id, owner_id="fake-provider-worker", workload_class="remote_llm_light"
            ),
            snapshot=ResourceSnapshot.test_snapshot(),
        )
        thread = threading.Thread(
            target=lambda: results.append(
                run_registered_operation(
                    runtime, execution_id, owner_id="fake-provider-worker", fencing_token=leader.fencing_token
                )
            )
        )
        thread.start()
        try:
            assert entered.wait(timeout=5)
            for url in ("/healthz", "/api/v1/overview", "/api/v1/workers/status"):
                started = time.monotonic()
                assert client.get(url).status_code == 200
                assert time.monotonic() - started < 0.5
            started = time.monotonic()
            response = client.post(
                f"/api/v1/executions/{execution_id}/cancel",
                json={"reason": "stop fake provider"},
                headers=headers,
            )
            assert response.status_code == 202
            assert response.json()["status"] == "cancel_requested"
            assert time.monotonic() - started < 0.5
        finally:
            release.set()
            thread.join(timeout=5)
        assert not thread.is_alive()
        assert results[0]["status"] == "cancelled"
    runtime.close()
