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


def test_chat_pipeline_intake_runs_product_owner_and_blocks_with_evidence_when_runtime_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)

    session_response = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"projectId": project["id"], "name": "Real intake session"},
    )
    assert session_response.status_code == 201
    session = session_response.json()["session"]

    chat_response = client.post(
        "/api/v1/chats",
        headers=headers,
        json={
            "projectId": project["id"],
            "sessionId": session["id"],
            "prompt": "Build a customer onboarding dashboard with audit-ready delivery.",
            "title": "Onboarding dashboard",
        },
    )
    assert chat_response.status_code == 201
    chat = chat_response.json()["chat"]

    pipeline_response = client.post(
        "/api/v1/pipelines",
        headers=headers,
        json={
            "projectId": project["id"],
            "sessionId": session["id"],
            "chatId": chat["id"],
            "title": "Onboarding dashboard",
            "productOwnerIntake": True,
        },
    )

    assert pipeline_response.status_code == 201
    pipeline = pipeline_response.json()["pipeline"]
    assert pipeline["status"] == "blocked"
    assert pipeline["metadata"]["productOwnerIntake"]["status"] == "runtime_unavailable"
    assert pipeline["metadata"]["productOwnerIntake"]["workspaceId"].startswith("workspace-")
    assert pipeline["metadata"]["productOwnerIntake"]["agentRunId"].startswith("agent-run-")
    assert pipeline["metadata"]["productOwnerIntake"]["evidencePackageId"].startswith("evidence-")
    assert pipeline["metadata"]["productOwnerIntake"]["loopId"].startswith("product-loop-")
    assert (
        "No executable ProductOwnerAgent runtime is configured"
        in pipeline["metadata"]["productOwnerIntake"]["reason"]
    )
    assert pipeline["stages"][0]["id"] == "intake"
    assert pipeline["stages"][0]["status"] == "blocked"
    assert (
        pipeline["stages"][0]["evidencePackageId"]
        == pipeline["metadata"]["productOwnerIntake"]["evidencePackageId"]
    )

    loop_state = client.get(f"/api/v1/projects/{project['id']}/product-loop").json()
    loop = next(
        item
        for item in loop_state["loops"]
        if item["id"] == pipeline["metadata"]["productOwnerIntake"]["loopId"]
    )
    assert loop["state"] == "blocked"
    assert loop["context"]["intake"]["pipelineId"] == pipeline["id"]
    assert loop["context"]["intake"]["chatId"] == chat["id"]
    assert loop["context"]["productOwner"]["status"] == "runtime_unavailable"

    overview = client.get("/api/v1/overview").json()
    assert any(
        item["id"] == pipeline["metadata"]["productOwnerIntake"]["agentRunId"]
        for item in overview["agentRuns"]
    )
    assert any(
        item["id"] == pipeline["metadata"]["productOwnerIntake"]["evidencePackageId"]
        for item in overview["evidencePackages"]
    )
