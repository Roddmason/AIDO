from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    return store, client, auth_headers(client)


def create_project_and_workspace(
    store: ControlPlaneFixture,
    tmp_path: Path,
    *,
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project_path = tmp_path / task_id
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text("# Security agent test project\n", encoding="utf-8")
    project = store.create_project(name=f"Security {task_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="security_agent",
        reason="security agent test workspace",
        isolation_type="directory",
    )
    return project, workspace


def security_request(project: dict[str, Any], workspace: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "projectId": project["id"],
        "workspaceId": workspace["id"],
        "taskId": "security-review",
        **extra,
    }


def test_security_agent_secret_like_key_in_workspace_blocks_with_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-secret")
    secret_file = Path(workspace["path"]) / "config.txt"
    secret = "sk-" + "test1234567890abcdef"
    secret_file.write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    assert any(finding["checkId"] == "secret_scan" for finding in body["findings"])
    assert secret not in json.dumps(body)
    assert body["findingsArtifact"]["id"].startswith("artifact-")
    assert body["evidencePackage"]["artifactIds"]
    assert body["filesScanned"][0]["hash"]
    assert body["agentRun"]["status"] == "failed"


def test_security_agent_clean_workspace_passes_with_file_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-clean")
    Path(workspace["path"], "package.json").write_text(
        json.dumps({"scripts": {"test": "pytest"}, "dependencies": {"left-pad": "1.3.0"}}),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "passed"
    assert body["verdict"] == "passed"
    assert body["findings"] == []
    assert body["filesScanned"]
    assert any(item["path"] == "package.json" for item in body["dependencyFiles"])
    assert all(item["hash"] for item in body["filesScanned"])
    assert body["evidencePackage"]["qaVerdict"] == "security_passed"
    assert body["agentRun"]["status"] == "completed"


def test_security_agent_path_traversal_candidate_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-traversal")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(project, workspace, pathsToCheck=["../outside.txt"]),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    finding = next(finding for finding in body["findings"] if finding["checkId"] == "path_traversal")
    assert finding["severity"] == "critical"
    assert "outside" in finding["message"].lower()


def test_security_agent_dangerous_docker_flags_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="security-docker")

    response = client.post(
        "/api/v1/agents/security/runs",
        headers=headers,
        json=security_request(
            project,
            workspace,
            commandCandidates=[
                {"label": "unsafe docker", "argv": ["docker", "run", "--privileged", "--network", "host", "alpine"]}
            ],
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["verdict"] == "blocked"
    findings = [finding for finding in body["findings"] if finding["checkId"] == "dangerous_command"]
    assert findings
    assert any("--privileged" in finding["message"] or "--network host" in finding["message"] for finding in findings)
    assert body["evidencePackage"]["qaVerdict"] == "security_blocked"
