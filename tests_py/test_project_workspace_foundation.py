from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.projects.discovery import discover_project_path


def test_project_discovery_reads_manifest_name_and_detects_runtimes(tmp_path: Path) -> None:
    project_path = tmp_path / "service-folder"
    project_path.mkdir()
    (project_path / "pom.xml").write_text(
        """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.aido</groupId>
  <artifactId>billing-service</artifactId>
  <name>Billing Service</name>
</project>
""",
        encoding="utf-8",
    )
    (project_path / "src" / "main" / "java").mkdir(parents=True)
    (project_path / "infra").mkdir()
    (project_path / "infra" / "main.tf").write_text('resource "null_resource" "example" {}', encoding="utf-8")

    discovery = discover_project_path(project_path)

    assert discovery["exists"] is True
    assert discovery["suggestedName"] == "Billing Service"
    assert discovery["templateId"] == "other"
    assert {source["manifest"] for source in discovery["manifestSources"]} >= {"pom.xml", "infra/main.tf"}
    runtime_ids = {runtime["id"] for runtime in discovery["detectedRuntimes"]}
    assert {"java-maven", "terraform"} <= runtime_ids


def test_project_discovery_prefers_package_name_and_detects_node_python(tmp_path: Path) -> None:
    project_path = tmp_path / "fullstack"
    project_path.mkdir()
    (project_path / "package.json").write_text(
        json.dumps({"name": "@aido/portal-web", "dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^6.0.0"}}),
        encoding="utf-8",
    )
    (project_path / "pyproject.toml").write_text('[project]\nname = "aido-api"\n', encoding="utf-8")

    discovery = discover_project_path(project_path)

    assert discovery["suggestedName"] == "portal-web"
    assert discovery["templateId"] == "react-vite"
    assert {runtime["id"] for runtime in discovery["detectedRuntimes"]} >= {"node", "python"}


def test_project_discovery_endpoint_requires_local_write_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    client = TestClient(app)

    denied = client.post("/api/v1/projects/discover", json={"path": str(tmp_path)})
    token = client.get("/api/v1/security/handshake").json()["token"]
    allowed = client.post(
        "/api/v1/projects/discover",
        json={"path": str(tmp_path)},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["discovery"]["path"] == str(tmp_path)
    runtime.close()


def test_local_directory_picker_endpoint_is_token_protected_and_mockable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]
    selected = tmp_path / "workspace-base"

    def fake_picker(*, title: str, initial_path: str | None = None) -> dict[str, str | None]:
        assert title == "Select workspace folder"
        assert initial_path == str(tmp_path)
        return {"status": "selected", "selectedPath": str(selected), "reason": None}

    monkeypatch.setattr("local_control_center.projects.api.select_directory_with_native_dialog", fake_picker)

    denied = client.post("/api/v1/local-paths/select-directory", json={"initialPath": str(tmp_path)})
    allowed = client.post(
        "/api/v1/local-paths/select-directory",
        json={"initialPath": str(tmp_path)},
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "selected"
    assert allowed.json()["selectedPath"] == str(selected)
    runtime.close()


def test_project_creation_under_workspace_base_stores_operational_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]
    workspace_base = tmp_path / "mis proyectos"

    response = client.post(
        "/api/v1/projects",
        json={
            "name": "Payments API",
            "workspaceBasePath": str(workspace_base),
            "projectDirectoryName": "payments-api",
            "templateId": "python-fastapi",
            "createDirectory": True,
            "metadata": {
                "detectedRuntimes": [{"id": "python", "kind": "backend", "manifest": "pyproject.toml"}],
                "manifestSources": [{"manifest": "pyproject.toml", "name": "payments-api"}],
            },
        },
        headers={"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"},
    )

    assert response.status_code == 201
    project = response.json()["project"]
    assert project["name"] == "Payments API"
    assert Path(project["path"]) == workspace_base / "payments-api"
    assert (workspace_base / "payments-api").is_dir()
    assert project["metadata"]["workspaceBasePath"] == str(workspace_base)
    assert project["metadata"]["projectDirectoryName"] == "payments-api"
    assert project["metadata"]["creationMode"] == "new_under_workspace"
    assert project["metadata"]["detectedRuntimes"][0]["id"] == "python"
    runtime.close()
