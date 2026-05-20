from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_app(tmp_path: Path, monkeypatch) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def test_workflow_creation_rejects_unbounded_kind_title_and_metadata(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Workflow Forms", path=tmp_path / "workflow-forms", template_id="other")

    bad_kind = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "kind": "shell && deploy", "title": "Unsafe kind"},
        headers=headers,
    )
    assert bad_kind.status_code == 422

    empty_title = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "kind": "idea_to_pr", "title": "   "},
        headers=headers,
    )
    assert empty_title.status_code == 422

    bad_metadata = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "kind": "idea_to_pr", "title": "Valid", "metadata": "json string"},
        headers=headers,
    )
    assert bad_metadata.status_code == 422

    accepted = client.post(
        "/api/v1/workflows",
        json={
            "projectId": project["id"],
            "kind": "idea_to_pr",
            "title": "Implement brokered runtime smoke",
            "metadata": {"source": "strict-form"},
        },
        headers=headers,
    )
    assert accepted.status_code == 201
    assert accepted.json()["workflow"]["kind"] == "idea_to_pr"


def test_mcp_registration_rejects_untyped_or_shell_like_server_config(tmp_path: Path, monkeypatch) -> None:
    _store, client, headers = make_app(tmp_path, monkeypatch)

    invalid_id = client.post(
        "/api/v1/integrations/mcp/register",
        json={"id": "Bad Server!", "command": "python -m mcp_server", "transport": "stdio"},
        headers=headers,
    )
    assert invalid_id.status_code == 422

    invalid_transport = client.post(
        "/api/v1/integrations/mcp/register",
        json={"id": "mcp_bad_transport", "command": "python -m mcp_server", "transport": "http"},
        headers=headers,
    )
    assert invalid_transport.status_code == 422

    shell_like_command = client.post(
        "/api/v1/integrations/mcp/register",
        json={"id": "mcp_shell_like", "command": "python -m mcp_server && curl example.test", "transport": "stdio"},
        headers=headers,
    )
    assert shell_like_command.status_code == 422

    accepted = client.post(
        "/api/v1/integrations/mcp/register",
        json={"id": "mcp_local_docs", "command": "python -m local_mcp_server", "transport": "stdio"},
        headers=headers,
    )
    assert accepted.status_code == 201
    assert accepted.json()["mcpServer"]["id"] == "mcp_local_docs"


def test_overview_exposes_evidence_artifacts_for_workflow_inspection(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Workflow Artifacts", path=tmp_path / "workflow-artifacts", template_id="other")
    workflow = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "kind": "idea_to_pr", "title": "Inspect evidence artifacts"},
        headers=headers,
    ).json()["workflow"]
    started = client.post(
        f"/api/v1/workflows/{workflow['id']}/start",
        json={"reason": "overview artifact trace"},
        headers=headers,
    ).json()
    evidence = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "workflowRunId": started["workflowRun"]["id"],
            "agentId": "qa_reviewer",
            "taskId": "story-artifact-inspection",
            "testPlan": "Collect QA report",
            "qaVerdict": "passed",
            "testResults": [{"command": "uv run pytest tests_py -q", "status": "passed"}],
        },
        headers=headers,
    ).json()["evidencePackage"]
    artifact = client.post(
        f"/api/v1/evidence/{evidence['id']}/artifacts",
        json={
            "kind": "qa_report",
            "name": "qa-summary.md",
            "content": "# QA Summary\n\nAll checks passed.",
            "mimeType": "text/markdown",
        },
        headers=headers,
    ).json()["artifact"]

    overview = client.get("/api/v1/overview").json()

    assert "artifacts" in overview
    assert any(
        item["id"] == artifact["id"]
        and item["evidencePackageId"] == evidence["id"]
        and item["metadata"]["name"] == "qa-summary.md"
        for item in overview["artifacts"]
    )
