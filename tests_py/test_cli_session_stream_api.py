from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_control_center.agents.cli_session_events import CliSessionEventStore
from local_control_center.shared.time import utc_now


def _client(tmp_path: Path):
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def test_start_session_is_write_guarded_and_validates_inputs(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token}

        # No write token is a 403.
        assert (
            client.post(
                "/api/v1/cli-sessions", json={"workspaceId": "w1", "argv": ["git", "status"]}
            ).status_code
            == 403
        )
        # An empty argv is rejected with 422 before anything runs.
        assert (
            client.post(
                "/api/v1/cli-sessions", json={"workspaceId": "w1", "argv": []}, headers=headers
            ).status_code
            == 422
        )
        # An unknown workspace is a 404.
        assert (
            client.post(
                "/api/v1/cli-sessions",
                json={"workspaceId": "missing", "argv": ["git", "status"]},
                headers=headers,
            ).status_code
            == 404
        )
    finally:
        runtime.close()


def test_events_endpoint_returns_incremental_pages(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        store = CliSessionEventStore(runtime.connection, project_id="p1")
        store.record_event("sess-x", "started", {"runtime": "codex_cli"})
        store.record_event("sess-x", "stdout_chunk", {"stream": "stdout", "text": "hi"})
        store.record_event("sess-x", "completed", {"returnCode": 0})

        response = client.get("/api/v1/cli-sessions/sess-x/events")
        assert response.status_code == 200
        body = response.json()
        assert [event["seq"] for event in body["events"]] == [1, 2, 3]
        assert [event["type"] for event in body["events"]] == ["started", "stdout_chunk", "completed"]
        assert body["latestSeq"] == 3
        assert body["running"] is False

        # Incremental polling: only events after the last seen seq come back.
        incremental = client.get("/api/v1/cli-sessions/sess-x/events", params={"afterSeq": 2}).json()
        assert [event["seq"] for event in incremental["events"]] == [3]
    finally:
        runtime.close()


def test_start_session_passes_execution_boundary_options(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    captured: dict[str, Any] = {}
    try:
        now = utc_now()
        runtime.connection.execute(
            """
            INSERT INTO workspaces
                (id, project_id, task_id, owner_agent_id, path, status, isolation_type,
                 metadata, created_at, updated_at)
            VALUES ('w1', 'p1', 'cli-session-test', 'developer_agent', ?, 'ready', 'git_worktree',
                    '{}', ?, ?)
            """,
            (str(tmp_path), now, now),
        )

        def fake_start_cli_session(*args: Any, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"id": "cli-session-1", "status": "running", "startedAt": now}

        monkeypatch.setattr(
            "local_control_center.agents.cli_session_stream_api.start_cli_session",
            fake_start_cli_session,
        )
        token = client.get("/api/v1/security/handshake").json()["token"]

        response = client.post(
            "/api/v1/cli-sessions",
            headers={"X-Local-Control-Token": token},
            json={
                "workspaceId": "w1",
                "runtime": "codex_cli",
                "argv": ["codex", "exec"],
                "timeoutSeconds": 120,
                "branchName": "codex/cli-session-hardening",
                "worktreeId": "worktree-123",
            },
        )

        assert response.status_code == 202
        assert captured["timeout_seconds"] == 120
        assert captured["branch_name"] == "codex/cli-session-hardening"
        assert captured["worktree_id"] == "worktree-123"
    finally:
        runtime.close()


def test_cancel_endpoint_is_write_guarded_and_reports_when_not_running(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        token = client.get("/api/v1/security/handshake").json()["token"]

        assert client.post("/api/v1/cli-sessions/sess-x/cancel").status_code == 403
        response = client.post("/api/v1/cli-sessions/sess-x/cancel", headers={"X-Local-Control-Token": token})
        assert response.status_code == 200
        assert response.json()["cancelled"] is False  # nothing is running under that id
    finally:
        runtime.close()
