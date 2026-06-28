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
    project_path = tmp_path / "workflow-source"
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text("# Workflow Product Owner test\n", encoding="utf-8")
    return store.create_project(name="Workflow Product Owner", path=project_path, template_id="other")


def test_idea_to_pr_start_runs_product_owner_and_blocks_with_evidence_when_runtime_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_project(store, tmp_path)

    created = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "projectId": project["id"],
            "kind": "idea_to_pr",
            "title": "Create onboarding analytics for trial conversion",
        },
    )
    assert created.status_code == 201
    workflow_id = created.json()["workflow"]["id"]

    started = client.post(
        f"/api/v1/workflows/{workflow_id}/start",
        headers=headers,
        json={"reason": "Start autonomous product workflow"},
    )

    assert started.status_code == 202
    body = started.json()
    assert body["workflow"]["status"] == "runtime_unavailable"
    assert body["workflowRun"]["status"] == "runtime_unavailable"
    intake = body["workflowRun"]["metadata"]["productOwnerIntake"]
    assert intake["status"] == "runtime_unavailable"
    assert intake["workspaceId"].startswith("workspace-")
    assert intake["agentRunId"].startswith("agent-run-")
    assert intake["evidencePackageId"].startswith("evidence-")
    assert intake["loopId"].startswith("product-loop-")
    assert "No executable ProductOwnerAgent runtime is configured" in intake["reason"]

    idea_step = next(step for step in body["workflowSteps"] if step["name"] == "idea_intake")
    assert idea_step["status"] == "blocked"
    assert idea_step["output"]["productOwnerIntake"]["evidencePackageId"] == intake["evidencePackageId"]
    assert idea_step["metadata"]["productOwnerIntake"]["status"] == "runtime_unavailable"
    backlog_step = next(step for step in body["workflowSteps"] if step["name"] == "backlog_generation")
    assert backlog_step["status"] == "pending"

    loop_state = client.get(f"/api/v1/projects/{project['id']}/product-loop").json()
    loop = next(item for item in loop_state["loops"] if item["id"] == intake["loopId"])
    assert loop["state"] == "blocked"
    assert loop["context"]["intake"]["source"] == "workflow_start"
    assert loop["context"]["intake"]["workflowId"] == workflow_id
    assert loop["context"]["productOwner"]["status"] == "runtime_unavailable"

    detail = client.get(f"/api/v1/workflows/{workflow_id}").json()
    run_detail = detail["workflowRunDetails"][0]
    assert any(item["id"] == intake["agentRunId"] for item in run_detail["agentRuns"])
    assert any(item["id"] == intake["evidencePackageId"] for item in run_detail["evidencePackages"])
    assert any(
        item["type"] == "workflow.idea_intake.runtime_unavailable"
        for item in detail["workflowEvents"]
    )
