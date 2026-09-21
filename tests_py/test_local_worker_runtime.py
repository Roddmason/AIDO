"""Tests for the local worker runtime: settings, API controls, and queued thread execution.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.jobs_approvals.worker import (
    _thread_status_for_product_loop_result,
    _thread_terminal_event_for_product_loop_result,
    execute_job,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.settings.repository import SettingsRepository
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workers.runtime import LocalWorkerRuntime
from local_control_center.workspaces_projects.repository import WorkspacesRepository


def _client(tmp_path: Path):
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, TestClient(_app(runtime))


def test_external_worker_preserves_thread_runtime_preflight(monkeypatch, tmp_path):
    from local_control_center.workers.runtime import WorkerPreflight

    runtime, client = _client(tmp_path)
    worker = LocalWorkerRuntime(db_path=runtime.db_path, cwd=tmp_path)
    calls = []
    try:
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        job_id = _queue_thread_job(client, runtime, thread["id"])
        client.post("/api/v1/workers/run-once", headers=_headers(runtime))
        monkeypatch.setattr(worker, "_govern_resources_if_due", lambda: True)
        monkeypatch.setattr(worker, "_ensure_leadership_watcher", lambda: None)
        # El gate bajo prueba es el de conversacion: el refresco de salud encolaria operaciones de
        # reparacion, que ese mismo gate deja pasar a proposito, y ocultaria lo que se verifica aca.
        monkeypatch.setattr(worker, "_refresh_runtime_health_if_due", lambda: None)

        def deny():
            calls.append("preflight")
            worker._stop_event.set()
            return WorkerPreflight(
                False,
                "No executable runtime is available for local worker execution.",
                [],
                None,
                "runtime",
                {"executable": False, "status": "configuration_required"},
            )

        def forbidden_batch():
            calls.append("batch")
            worker._stop_event.set()

        monkeypatch.setattr(worker, "preflight", deny)
        monkeypatch.setattr(worker, "_run_batch_once", forbidden_batch)
        worker.run_forever()
        assert calls == ["preflight"]
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        assert ("runtime_not_executable", "run_worker_once") in _pending_remediation_actions(
            runtime.connection, thread["id"]
        )
    finally:
        worker.stop(reason="isolated external worker preflight test")
        runtime.close()


def test_control_operation_can_repair_an_older_queued_conversation(tmp_path):
    runtime, client = _client(tmp_path)
    try:
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        thread_job_id = _queue_thread_job(client, runtime, thread["id"])
        response = client.post(
            "/api/v1/projects",
            headers=_headers(runtime),
            json={
                "name": "Repair project",
                "path": str(tmp_path / "repair"),
                "templateId": "other",
                "createDirectory": True,
            },
        )
        assert response.status_code == 202
        jobs = JobsRepository(runtime.connection)
        assert jobs.peek_next_job()["id"] == response.json()["executionId"]
        assert jobs.get_job(thread_job_id)["status"] == "queued"
    finally:
        runtime.close()


def test_external_worker_never_downgrades_a_lost_fence_to_unfenced_claim(monkeypatch, tmp_path):
    runtime, client = _client(tmp_path)
    worker = LocalWorkerRuntime(db_path=runtime.db_path, cwd=tmp_path)
    seen = []
    try:
        client.post("/api/v1/workers/run-once", headers=_headers(runtime))
        monkeypatch.setattr(worker, "_govern_resources_if_due", lambda: True)
        monkeypatch.setattr(worker, "_ensure_leadership_watcher", lambda: None)

        def lose_fence_during_preflight():
            worker._fencing_token = None
            worker._stop_event.set()
            return True

        def capture_batch(_worker, **kwargs):
            seen.append(kwargs["fencing_token"])
            return []

        monkeypatch.setattr(worker, "_queued_job_preflight", lose_fence_during_preflight)
        monkeypatch.setattr("local_control_center.workers.runtime.ConcurrentWorker.run_batch", capture_batch)
        worker.run_forever()
        assert len(seen) == 1 and isinstance(seen[0], int), seen
    finally:
        worker.stop(reason="isolated fencing race regression")
        runtime.close()


def _app(runtime):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app

    return create_app(runtime=runtime, static_dir=None)


def _headers(runtime) -> dict[str, str]:
    return {"X-Local-Control-Token": runtime.get_handshake()["token"]}


def _project(runtime, tmp_path: Path) -> dict[str, Any]:
    return ProjectsRepository(runtime.connection).create_project(
        name="Worker Runtime",
        path=tmp_path / "worker-runtime",
        template_id="other",
    )


def _thread(client: TestClient, runtime, project_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/threads",
        headers=_headers(runtime),
        json={
            "projectId": project_id,
            "ownerType": "workspace",
            "ownerId": "workspace-worker",
            "title": "Worker runtime thread",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["thread"]


def _queue_thread_job(client: TestClient, runtime, thread_id: str) -> str:
    response = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        headers=_headers(runtime),
        json={"content": "Add a worker status endpoint."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["thread"]["status"] == "queued"
    return body["run"]["jobId"]


def _write_fake_gitleaks(bin_dir: Path) -> None:
    script = bin_dir / "gitleaks_impl.py"
    script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    command = bin_dir / ("gitleaks.cmd" if os.name == "nt" else "gitleaks")
    if os.name == "nt":
        command.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        command.write_text(f'#!/usr/bin/env sh\n"{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        command.chmod(0o755)


def _executable_runtime_statuses() -> list[dict[str, Any]]:
    return [
        {
            "id": "controlled_runtime",
            "kind": "cli",
            "displayName": "Controlled runtime",
            "executable": True,
            "available": True,
            "configured": True,
            "reason": "Controlled runtime is executable for this test.",
            "capabilities": ["code_edit"],
        }
    ]


def _pending_remediation_actions(connection, thread_id: str) -> set[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT blocker_type, action_type
        FROM remediation_actions
        WHERE thread_id = ? AND status = 'pending'
        ORDER BY created_at ASC, rowid ASC
        """,
        (thread_id,),
    ).fetchall()
    return {(row["blocker_type"], row["action_type"]) for row in rows}


def _execute_worker_once(runtime, tmp_path: Path) -> tuple[LocalWorkerRuntime, dict[str, Any]]:
    worker = LocalWorkerRuntime.from_settings(
        connection=runtime.connection,
        db_path=runtime.db_path,
        cwd=tmp_path,
    )
    return worker, worker.run_once()


def test_worker_preserves_waiting_decision_for_awaiting_user_product_loop() -> None:
    assert _thread_status_for_product_loop_result("awaiting_user") == "waiting_decision"
    assert _thread_terminal_event_for_product_loop_result("awaiting_user") == "decision_required"


def test_worker_settings_default_autostart_is_disabled(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/settings?projectId=proj-worker")
        assert response.status_code == 200
        general = {item["key"]: item for item in response.json()["general"]}
        assert general["worker.autostart"]["value"] is False
        assert general["worker.autostart"]["origin"] == "default"
        assert general["worker.maxConcurrentJobs"]["value"] == 1
    finally:
        runtime.close()


@pytest.mark.usefixtures("controlled_domain_host")
def test_worker_run_once_executes_queued_thread_job(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gitleaks(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: _executable_runtime_statuses(),
    )

    captured: dict[str, str] = {}

    def fake_run_user_message(self, **kwargs):
        captured["thread_id"] = kwargs["thread_id"]
        captured["project_id"] = kwargs["project_id"]
        return {
            "status": "awaiting_approval",
            "reason": "Controlled worker execution finished.",
            "loop": {"id": "loop-worker"},
            "evidencePackage": {"id": "evidence-worker"},
        }

    monkeypatch.setattr(
        "local_control_center.product_loop.coordinator.ProductLoopCoordinator.run_user_message",
        fake_run_user_message,
    )

    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        job_id = _queue_thread_job(client, runtime, thread["id"])

        response = client.post("/api/v1/workers/run-once", headers=headers)

        assert response.status_code == 202, response.text
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        _worker, body = _execute_worker_once(runtime, tmp_path)
        assert body["status"] == "completed"
        assert body["claimedJobs"] == 1
        assert body["runs"][0]["job"]["id"] == job_id
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "completed"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "awaiting_approval"
        assert captured == {"thread_id": thread["id"], "project_id": project["id"]}
        event_types = [
            event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])
        ]
        assert "worker_started" in event_types
        assert "worker_claimed" in event_types
    finally:
        runtime.close()


@pytest.mark.usefixtures("controlled_domain_host")
def test_worker_run_once_preserves_product_loop_job_run_metadata(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gitleaks(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: _executable_runtime_statuses(),
    )

    captured: dict[str, Any] = {}

    def fake_run_user_message(self, **kwargs):
        captured.update(kwargs)
        return {
            "status": "awaiting_approval",
            "reason": "Controlled worker execution finished.",
            "loop": {"id": "loop-worker-metadata"},
            "evidencePackage": {"id": "evidence-worker-metadata"},
        }

    monkeypatch.setattr(
        "local_control_center.product_loop.coordinator.ProductLoopCoordinator.run_user_message",
        fake_run_user_message,
    )

    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        approved_resources = [
            {
                "role": "backend_engineer",
                "providerId": "nvidia_nim",
                "model": "nvidia/nemotron-coder",
                "runtime": "api",
            }
        ]
        job = JobsRepository(runtime.connection).create_job(
            project_id=project["id"],
            kind="thread.product_loop.run",
            payload={
                "threadId": thread["id"],
                "messageId": "thread-message-worker-metadata",
                "projectId": project["id"],
                "message": "Implement worker metadata preservation.",
                "title": thread["title"],
                "root": str(tmp_path),
                "preferredRuntime": "nvidia_nim",
                "qaCommands": ["uv run pytest -q"],
                "planOnly": True,
                "planOnlyOfLoopId": "product-loop-plan-only-source",
                "planOnlyStage": "runtime",
                "planOnlyReason": "Runtime failed after planning.",
                "planOnlyQueuedAt": "2026-01-02T00:00:00.000Z",
                "remediationActionId": "remediation-plan-only-1",
                "approvedResourceSelections": approved_resources,
                "retryOfLoopId": "product-loop-retry-source",
                "retryStage": "resource_manager",
                "retryReason": "Resource approval was completed.",
                "retryQueuedAt": "2026-01-01T00:00:00.000Z",
                "continueOfLoopId": "product-loop-original",
                "feedbackId": "feedback-continue-1",
                "continueReason": "Continue after operator requested changes.",
                "runMetadata": {
                    "teamMode": "critical",
                    "risk": "high",
                    "allowUnknownCost": True,
                    "allow_unknown_cost": True,
                    "requireApprovalForUnknownCost": False,
                    "require_approval_for_unknown_cost": False,
                    "privacyLevel": "local_private",
                    "userMode": "aido_decide",
                    "autonomy": "guided",
                    "researchPolicy": {"requireOfficialSources": True},
                },
            },
        )["job"]
        ThreadsRepository(runtime.connection).set_status(thread["id"], "queued")

        response = client.post("/api/v1/workers/run-once", headers=headers)

        assert response.status_code == 202, response.text
        _worker, worker_result = _execute_worker_once(runtime, tmp_path)
        assert worker_result["status"] == "completed"
        metadata = captured["run_metadata"]
        assert captured["preferred_runtime"] == "nvidia_nim"
        assert captured["qa_commands"] == ["uv run pytest -q"]
        assert metadata["jobId"] == job["id"]
        assert metadata["threadId"] == thread["id"]
        assert metadata["messageId"] == "thread-message-worker-metadata"
        assert metadata["planOnly"] is True
        assert metadata["planOnlyOfLoopId"] == "product-loop-plan-only-source"
        assert metadata["planOnlyStage"] == "runtime"
        assert metadata["planOnlyReason"] == "Runtime failed after planning."
        assert metadata["planOnlyQueuedAt"] == "2026-01-02T00:00:00.000Z"
        assert metadata["remediationActionId"] == "remediation-plan-only-1"
        assert metadata["retryOfLoopId"] == "product-loop-retry-source"
        assert metadata["retryStage"] == "resource_manager"
        assert metadata["retryReason"] == "Resource approval was completed."
        assert metadata["retryQueuedAt"] == "2026-01-01T00:00:00.000Z"
        assert metadata["continueOfLoopId"] == "product-loop-original"
        assert metadata["feedbackId"] == "feedback-continue-1"
        assert metadata["continueReason"] == "Continue after operator requested changes."
        assert metadata["approvedResourceSelections"] == approved_resources
        assert metadata["teamMode"] == "critical"
        assert metadata["risk"] == "high"
        assert "allowUnknownCost" not in metadata
        assert "allow_unknown_cost" not in metadata
        assert "requireApprovalForUnknownCost" not in metadata
        assert "require_approval_for_unknown_cost" not in metadata
        assert metadata["privacyLevel"] == "local_private"
        assert metadata["userMode"] == "aido_decide"
        assert metadata["autonomy"] == "guided"
        assert metadata["researchPolicy"] == {"requireOfficialSources": True}
    finally:
        runtime.close()


def test_worker_run_once_leaves_job_queued_when_preflight_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: [],
    )
    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        job_id = _queue_thread_job(client, runtime, thread["id"])

        response = client.post("/api/v1/workers/run-once", headers=headers)

        assert response.status_code == 202, response.text
        _worker, body = _execute_worker_once(runtime, tmp_path)
        assert body["status"] == "blocked"
        assert body["claimedJobs"] == 0
        assert "runtime" in body["reason"].lower()
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "queued"
        event_types = [
            event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])
        ]
        assert "worker_failed" in event_types
        actions = _pending_remediation_actions(runtime.connection, thread["id"])
        assert ("runtime_not_executable", "open_settings_section") in actions
        assert ("runtime_not_executable", "validate_runtime") in actions
        assert ("runtime_not_executable", "run_worker_once") in actions
        assert ("runtime_not_executable", "retry_loop") not in actions
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", ["heavy_workload_capacity", "aggregate_memory_budget"])
def test_worker_defers_capacity_to_atomic_job_admission(monkeypatch, tmp_path, reason):
    """A job's existing reservation must not make its runtime look unconfigured."""
    provider = {
        "id": "claude_code_cli",
        "executable": False,
        "configured": True,
        "authenticated": True,
        "available": True,
        "healthy": True,
        "globallyEnabled": True,
        "projectEnabled": True,
        "policyAllowed": True,
        "blockingReasons": [reason],
    }
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: [provider],
    )
    monkeypatch.setattr("local_control_center.workers.runtime._which_gitleaks", lambda: "gitleaks")
    worker = LocalWorkerRuntime(db_path=tmp_path / "worker.sqlite", cwd=tmp_path)
    assert worker.preflight().ok
    # An independent authentication/policy failure must still block before claiming.
    provider["blockingReasons"].append("authentication_required")
    assert not worker.preflight().ok


def test_worker_run_once_requires_gitleaks(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "empty-bin"
    fake_bin.mkdir()
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: _executable_runtime_statuses(),
    )
    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        job_id = _queue_thread_job(client, runtime, thread["id"])

        response = client.post("/api/v1/workers/run-once", headers=headers)

        assert response.status_code == 202, response.text
        _worker, body = _execute_worker_once(runtime, tmp_path)
        assert body["status"] == "blocked"
        assert "gitleaks" in body["reason"].lower()
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        event_types = [
            event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])
        ]
        assert "worker_failed" in event_types
        actions = _pending_remediation_actions(runtime.connection, thread["id"])
        assert ("gitleaks_missing", "open_settings_section") in actions
        assert ("gitleaks_missing", "run_gitleaks") in actions
        assert ("gitleaks_missing", "run_worker_once") in actions
        assert ("gitleaks_missing", "retry_loop") not in actions
    finally:
        runtime.close()


@pytest.mark.usefixtures("controlled_domain_host")
def test_worker_product_loop_exception_creates_worker_retry_remediation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gitleaks(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: _executable_runtime_statuses(),
    )

    calls = {"total": 0}

    def flaky_product_loop(_self, **_kwargs):
        calls["total"] += 1
        if calls["total"] == 1:
            raise RuntimeError("controlled Product Loop coordinator crash")
        return {
            "status": "awaiting_approval",
            "reason": "Controlled worker retry finished.",
            "loop": {"id": "loop-worker-retry"},
            "evidencePackage": {"id": "evidence-worker-retry"},
        }

    monkeypatch.setattr(
        "local_control_center.product_loop.coordinator.ProductLoopCoordinator.run_user_message",
        flaky_product_loop,
    )

    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        job_id = _queue_thread_job(client, runtime, thread["id"])
        service = BlockerRemediationService(runtime.connection, root=tmp_path)
        generic_remediations = service.list_for_thread(
            thread_id=thread["id"],
            worker_status={"running": False},
        )
        generic_action = next(
            item for item in generic_remediations if item["actionType"] == "run_worker_once"
        )
        assert "details" not in generic_action["payload"]

        response = client.post("/api/v1/workers/run-once", headers=headers)

        assert response.status_code == 202, response.text
        worker, body = _execute_worker_once(runtime, tmp_path)
        runtime.local_worker_runtime = worker
        assert body["status"] == "failed"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "blocked"
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "failed"
        actions = _pending_remediation_actions(runtime.connection, thread["id"])
        assert ("worker_not_running", "run_worker_once") in actions
        action_row = runtime.connection.execute(
            """
            SELECT id
            FROM remediation_actions
            WHERE thread_id = ?
              AND blocker_type = 'worker_not_running'
              AND action_type = 'run_worker_once'
              AND status = 'pending'
            """,
            (thread["id"],),
        ).fetchone()
        enriched_action = service.repository.get(action_row["id"])
        assert enriched_action["id"] == generic_action["id"]
        assert enriched_action["payload"]["details"]["jobId"] == job_id

        execution = service.execute(
            action_row["id"],
            platform=runtime,
        )

        assert execution["execution"]["status"] == "queued"
        assert execution["execution"]["jobRetry"]["job"]["id"] == job_id
        assert execution["execution"]["jobRetry"]["job"]["status"] == "queued"
        assert execution["remediation"]["status"] == "resolved"
        # Remediation requests one durable worker cycle; it must not run the worker in the API.
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        assert calls["total"] == 1
        retried = worker.run_once()
        assert retried["status"] == "completed", retried
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "completed"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "awaiting_approval"
        assert calls["total"] == 2
    finally:
        runtime.close()


def test_run_worker_once_remediation_resolves_when_referenced_job_already_completed(
    tmp_path: Path,
) -> None:
    class IdleWorker:
        def __init__(self) -> None:
            self.calls = 0

        def run_once(self) -> dict[str, Any]:
            self.calls += 1
            return {"status": "idle", "reason": "No queued jobs."}

    class Platform:
        def __init__(self, worker: IdleWorker) -> None:
            self.local_worker_runtime = worker

    runtime, _unused_client = _client(tmp_path)
    try:
        project = _project(runtime, tmp_path)
        thread = ThreadsRepository(runtime.connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-worker",
            title="Already recovered worker thread",
        )
        job = JobsRepository(runtime.connection).create_job(
            project_id=project["id"],
            kind="thread.product_loop.run",
            status="completed",
            payload={"threadId": thread["id"], "message": "Already completed."},
        )["job"]
        service = BlockerRemediationService(runtime.connection, root=tmp_path)
        actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="loop-worker-completed",
            stage="worker",
            reason="Worker job failed before this remediation was executed.",
            details={"jobId": job["id"], "status": "failed"},
        )
        action = next(item for item in actions if item["actionType"] == "run_worker_once")
        worker = IdleWorker()

        execution = service.execute(action["id"], platform=Platform(worker))

        assert execution["execution"]["status"] == "completed"
        assert execution["execution"]["jobRetry"]["status"] == "completed"
        assert execution["execution"]["jobRetry"]["job"]["id"] == job["id"]
        assert execution["remediation"]["status"] == "resolved"
        assert worker.calls == 0
    finally:
        runtime.close()


def test_thread_research_job_block_creates_network_remediation(monkeypatch, tmp_path: Path) -> None:
    def blocked_research_run(_self, _payload):
        return {
            "status": "research_blocked",
            "reason": "ResearchAgent web search failed: <urlopen error network unreachable>",
            "remediation": {"action": "check_network_access"},
            "reportArtifact": None,
        }

    monkeypatch.setattr(
        "local_control_center.jobs_approvals.worker.ResearchAgentRunner.run",
        blocked_research_run,
    )
    runtime, client = _client(tmp_path)
    try:
        project_path = tmp_path / "worker-runtime"
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / "README.md").write_text("research worker fixture\n", encoding="utf-8")
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        workspace = WorkspacesRepository(runtime.connection, root=tmp_path).allocate_workspace(
            project_id=project["id"],
            task_id="thread-research-network-block",
            agent_id="research_agent",
            reason="Research worker blocker test",
        )
        job = JobsRepository(runtime.connection).create_job(
            project_id=project["id"],
            kind="thread.research.run",
            payload={
                "threadId": thread["id"],
                "projectId": project["id"],
                "workspaceId": workspace["id"],
                "query": "Research official runtime provider documentation.",
                "root": str(tmp_path),
            },
        )["job"]

        result = execute_job(job, connection=runtime.connection, worker_id="worker-test")

        actions = {
            (row["blocker_type"], row["action_type"])
            for row in runtime.connection.execute(
                """
                SELECT blocker_type, action_type
                FROM remediation_actions
                WHERE thread_id = ? AND status = 'pending'
                """,
                (thread["id"],),
            ).fetchall()
        }
        assert result["metadata"]["researchStatus"] == "research_blocked"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "blocked"
        assert ("research_required", "check_network_access") in actions
        assert ("research_required", "run_worker_once") in actions
        assert ("research_required", "retry_loop") not in actions
    finally:
        runtime.close()


def test_worker_autostart_disabled_keeps_queued_job_and_status_stopped(tmp_path: Path) -> None:
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        runtime.init()
        SettingsRepository(runtime.connection).set_value("worker.autostart", "general", None, False)
        with TestClient(_app(runtime)) as client:
            project = _project(runtime, tmp_path)
            thread = _thread(client, runtime, project["id"])
            job_id = _queue_thread_job(client, runtime, thread["id"])

            response = client.get("/api/v1/workers/status")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["running"] is False
        assert body["paused"] is True
        assert body["autostart"] is False
        assert body["desiredState"] == "paused"
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
    finally:
        runtime.close()


def test_worker_pause_records_thread_event_for_queued_job(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        project = _project(runtime, tmp_path)
        thread = _thread(client, runtime, project["id"])
        job_id = _queue_thread_job(client, runtime, thread["id"])

        response = client.post("/api/v1/workers/pause", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["paused"] is True
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        event_types = [
            event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])
        ]
        assert "worker_paused" in event_types
    finally:
        runtime.close()


def test_worker_status_pause_and_resume_reflect_runtime_state(monkeypatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gitleaks(fake_bin)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(
        "local_control_center.workers.runtime.RuntimeStatusService.list_provider_statuses",
        lambda _service: _executable_runtime_statuses(),
    )
    runtime, client = _client(tmp_path)
    try:
        headers = _headers(runtime)
        resumed = client.post("/api/v1/workers/resume", headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["running"] is False
        assert resumed.json()["connected"] is False
        assert resumed.json()["desiredState"] == "running"

        paused = client.post("/api/v1/workers/pause", headers=headers)
        assert paused.status_code == 200, paused.text
        assert paused.json()["paused"] is True
        assert paused.json()["running"] is False

        status = client.get("/api/v1/workers/status")
        assert status.status_code == 200
        assert status.json()["paused"] is True
    finally:
        runtime.close()


def test_run_batch_prunes_stale_http_telemetry(tmp_path: Path) -> None:
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema
    from local_control_center.workers.runtime import LocalWorkerRuntime

    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        connection.execute(
            "INSERT INTO events (id, job_id, project_id, type, payload, created_at)"
            " VALUES ('event-old-http', NULL, NULL, 'telemetry.http.request', '{}',"
            " '2020-01-01T00:00:00.000Z')"
        )
        connection.execute(
            "INSERT INTO events (id, job_id, project_id, type, payload, created_at)"
            " VALUES ('event-old-domain', NULL, NULL, 'job.created', '{}', '2020-01-01T00:00:00.000Z')"
        )

    worker_runtime = LocalWorkerRuntime(db_path=db_path, cwd=tmp_path)
    worker_runtime._run_batch_once()

    with closing(open_sqlite_connection(db_path)) as connection, connection:
        survivors = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM events WHERE id IN ('event-old-http', 'event-old-domain')"
            ).fetchall()
        }
    assert survivors == {"event-old-domain"}

    with closing(open_sqlite_connection(db_path)) as connection, connection:
        connection.execute(
            "INSERT INTO events (id, job_id, project_id, type, payload, created_at)"
            " VALUES ('event-old-http-2', NULL, NULL, 'telemetry.http.request', '{}',"
            " '2020-01-01T00:00:00.000Z')"
        )
    worker_runtime._run_batch_once()
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        throttled = connection.execute("SELECT id FROM events WHERE id = 'event-old-http-2'").fetchone()
    assert throttled is not None
