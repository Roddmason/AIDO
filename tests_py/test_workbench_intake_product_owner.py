from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    for name in [
        "AIDO_CODEX_COMMAND",
        "AIDO_CLAUDE_COMMAND",
        "AIDO_ENABLE_CLI_RUNTIMES",
        "AIDO_ENABLE_REAL_PROVIDER_CALLS",
        "AIDO_OPENAI_COMPATIBLE_API_KEY",
        "AIDO_OLLAMA_BASE_URL",
    ]:
        monkeypatch.delenv(name, raising=False)
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def create_project(store: ControlPlaneFixture, tmp_path: Path) -> dict[str, str]:
    project_path = tmp_path / "workspace-source"
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text("# Intake product owner test\n", encoding="utf-8")
    return store.create_project(name="Workbench intake", path=project_path, template_id="other")


def test_thread_intake_queues_product_loop_without_legacy_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)

    thread_response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project["id"],
            "ownerType": "workspace",
            "ownerId": project["id"],
            "title": "Onboarding dashboard",
        },
    )
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["thread"]["id"]

    message_response = client.post(
        f"/api/v1/threads/{thread_id}/messages",
        headers=headers,
        json={"content": "Build a customer onboarding dashboard with audit-ready delivery."},
    )

    assert message_response.status_code == 200
    result = message_response.json()
    assert result["thread"]["id"] == thread_id
    assert result["run"]["status"] in {"queued", "blocked"}
    assert result["messages"][0]["threadId"] == thread_id

    jobs = store.jobs.list_jobs(project_id=project["id"])
    assert any(job["kind"] == "thread.product_loop.run" for job in jobs)

    overview = client.get("/api/v1/overview").json()
    assert "sessions" not in overview
    assert "chats" not in overview
    assert "pipelines" not in overview
    assert any(item["id"] == thread_id for item in overview["threads"])

    assert store.connection.execute("SELECT COUNT(*) AS total FROM sessions").fetchone()["total"] == 0
    assert store.connection.execute("SELECT COUNT(*) AS total FROM chats").fetchone()["total"] == 0
    assert store.connection.execute("SELECT COUNT(*) AS total FROM pipelines").fetchone()["total"] == 0
