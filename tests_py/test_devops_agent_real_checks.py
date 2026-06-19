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


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
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


def _commands_by_label(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(command["label"]): command for command in body["commands"]}


def _write_release_package(path: str | Path, *, scripts: dict[str, str]) -> None:
    Path(path, "package.json").write_text(
        json.dumps(
            {
                "scripts": scripts,
                "packageManager": "pnpm@10.24.0",
                "engines": {"node": ">=0.0.0"},
            }
        ),
        encoding="utf-8",
    )


def test_devops_agent_runs_release_toolchain_and_default_quality_with_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-release-toolchain")
    _write_release_package(workspace["path"], scripts={"quality": "node --version"})

    response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace, buildScripts=[]),
    )

    assert response.status_code == 202
    body = response.json()
    commands = _commands_by_label(body)
    for label in ("Tool version: node", "Tool version: uv", "Tool version: corepack", "Tool version: pnpm"):
        result = commands[label]
        assert result["status"] in {"passed", "skipped_with_reason"}
        assert result["command"]
        assert result["outputArtifactId"].startswith("artifact-")
        assert result["artifactHashes"]["outputArtifactHash"]
    quality = commands["Quality script: quality"]
    assert quality["command"] == "corepack pnpm@10.24.0 run quality"
    assert quality["status"] in {"passed", "skipped_with_reason"}
    assert quality["outputArtifactId"].startswith("artifact-")
    assert body["versions"]["node"]["required"] == ">=0.0.0"
    assert body["versions"]["node"]["status"] in {"passed", "skipped_with_reason"}
    assert body["versions"]["uv"]["command"] == "uv --version"
    assert body["versions"]["corepack"]["command"] == "corepack --version"
    assert body["versions"]["pnpm"]["command"] == "corepack pnpm@10.24.0 --version"
    assert body["evidencePackage"]["artifactIds"]


def test_devops_agent_accepts_configurable_quality_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-quality-subset")
    _write_release_package(
        workspace["path"],
        scripts={
            "quality": 'node -e "process.exit(1)"',
            "test:py": "node --version",
        },
    )

    response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace, buildScripts=[], qualityScripts=["test:py"]),
    )

    assert response.status_code == 202
    commands = _commands_by_label(response.json())
    assert "Quality script: test:py" in commands
    assert "Quality script: quality" not in commands


def test_devops_agent_missing_release_tools_are_skipped_with_reason_not_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="devops-missing-tools")
    _write_release_package(workspace["path"], scripts={"quality": "node --version"})

    response = client.post(
        "/api/v1/agents/devops/runs",
        headers=headers,
        json=devops_request(project, workspace, buildScripts=[], qualityScripts=[]),
    )

    assert response.status_code == 202
    body = response.json()
    tool_results = [result for result in body["commands"] if str(result["label"]).startswith("Tool version:")]
    assert tool_results
    assert {result["status"] for result in tool_results} == {"skipped_with_reason"}
    assert body["status"] == "risk"
    assert body["evidencePackage"]["qaVerdict"] == "devops_risk"
    assert not [result for result in tool_results if result["status"] == "failed"]


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
        json=devops_request(project, workspace, buildScripts=["build"], qualityScripts=[]),
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
        json=devops_request(project, workspace, buildScripts=["build"], qualityScripts=[]),
    )

    assert response.status_code == 202
    body = response.json()
    command = next(result for result in body["commands"] if result["label"] == "Build script: build")
    assert body["status"] == "passed"
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert command["status"] == "passed"
    assert command["exitCode"] == 0
    assert command["toolCallId"]
    assert command["artifactHashes"]["outputArtifactHash"]
    assert body["configArtifact"]["id"].startswith("artifact-")
