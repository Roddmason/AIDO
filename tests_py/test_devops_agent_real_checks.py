from __future__ import annotations

import json
import shutil
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
    (project_path / "README.md").write_text("# DevOps agent test project\n", encoding="utf-8")
    project = store.create_project(name=f"DevOps {task_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="devops_agent",
        reason="devops agent test workspace",
        isolation_type="directory",
    )
    return project, workspace


def devops_request(project: dict[str, Any], workspace: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "projectId": project["id"],
        "workspaceId": workspace["id"],
        "taskId": "devops-review",
        **extra,
    }


def test_devops_agent_without_docker_does_not_fail_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_which = shutil.which
    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.shutil.which",
        lambda name: None if name == "docker" else real_which(name),
    )
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-no-docker")

    status_response = client.get("/api/v1/agents/devops/status")
    run_response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace, dockerHealthcheck=True),
    )

    assert status_response.status_code == 200
    assert status_response.json()["devopsAgent"]["executable"] is True
    assert run_response.status_code == 202
    body = run_response.json()
    assert body["docker"]["available"] is False
    assert body["docker"]["healthcheck"]["status"] == "skipped_with_reason"
    assert "not available" in body["docker"]["healthcheck"]["reason"].lower()
    assert body["status"] in {"passed", "risk"}
    assert body["evidencePackage"]["artifactIds"]


def test_devops_agent_missing_build_command_reports_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-missing-build")
    Path(workspace["path"], "package.json").write_text(
        json.dumps({"scripts": {"test": "node --version"}, "packageManager": "pnpm@10.24.0"}),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace, buildScripts=["build"]),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "risk"
    missing = next(result for result in body["commands"] if result["status"] == "skipped_with_reason")
    assert missing["label"] == "Build script: build"
    assert "missing" in missing["reason"].lower()
    assert body["evidencePackage"]["qaVerdict"] == "devops_risk"


def test_devops_agent_detects_deprecated_legacy_powershell_scripts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-legacy-script")
    scripts_dir = Path(workspace["path"], "local-control-center", "scripts")
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "legacy-build.ps1").write_text("npm run build\n", encoding="utf-8")

    response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    finding = next(finding for finding in body["configFindings"] if finding["checkId"] == "legacy_script")
    assert finding["severity"] == "medium"
    assert finding["location"]["path"].endswith("legacy-build.ps1")
    assert body["status"] == "risk"


def test_devops_agent_existing_build_command_executes_through_broker_with_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-build")
    Path(workspace["path"], "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "node --version"},
                "packageManager": "pnpm@10.24.0",
                "engines": {"node": ">=18"},
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace, buildScripts=["build"]),
    )

    assert response.status_code == 202
    body = response.json()
    command = next(result for result in body["commands"] if result["label"] == "Build script: build")
    assert body["status"] == "passed"
    assert command["status"] == "passed"
    assert command["exitCode"] == 0
    assert command["toolCallId"]
    assert command["artifactHashes"]["outputArtifactHash"]
    assert body["configArtifact"]["id"].startswith("artifact-")
