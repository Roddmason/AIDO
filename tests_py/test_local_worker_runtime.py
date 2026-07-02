"""Tests for the local worker runtime: settings, API controls, and queued thread execution.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.repository import SettingsRepository
from local_control_center.threads.repository import ThreadsRepository


def _client(tmp_path: Path):
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, TestClient(_app(runtime))


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
        command.write_text(f"@echo off\n\"{sys.executable}\" \"{script}\" %*\n", encoding="utf-8")
    else:
        command.write_text(f"#!/usr/bin/env sh\n\"{sys.executable}\" \"{script}\" \"$@\"\n", encoding="utf-8")
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


def test_worker_settings_default_autostart_is_enabled(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/settings?projectId=proj-worker")
        assert response.status_code == 200
        general = {item["key"]: item for item in response.json()["general"]}
        assert general["worker.autostart"]["value"] is True
        assert general["worker.autostart"]["origin"] == "default"
    finally:
        runtime.close()


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

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["claimedJobs"] == 1
        assert body["runs"][0]["job"]["id"] == job_id
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "completed"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "awaiting_approval"
        assert captured == {"thread_id": thread["id"], "project_id": project["id"]}
        event_types = [event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])]
        assert "worker_started" in event_types
        assert "worker_claimed" in event_types
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

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "blocked"
        assert body["claimedJobs"] == 0
        assert "runtime" in body["reason"].lower()
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        assert ThreadsRepository(runtime.connection).get_thread(thread["id"])["status"] == "queued"
        event_types = [event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])]
        assert "worker_failed" in event_types
    finally:
        runtime.close()


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

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "blocked"
        assert "gitleaks" in body["reason"].lower()
        assert JobsRepository(runtime.connection).get_job(job_id)["status"] == "queued"
        event_types = [event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])]
        assert "worker_failed" in event_types
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
        assert body["paused"] is False
        assert body["autostart"] is False
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
        event_types = [event["type"] for event in ThreadsRepository(runtime.connection).list_events(thread["id"])]
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
        assert resumed.json()["running"] is True

        paused = client.post("/api/v1/workers/pause", headers=headers)
        assert paused.status_code == 200, paused.text
        assert paused.json()["paused"] is True
        assert paused.json()["running"] is False

        status = client.get("/api/v1/workers/status")
        assert status.status_code == 200
        assert status.json()["paused"] is True
    finally:
        runtime.close()
