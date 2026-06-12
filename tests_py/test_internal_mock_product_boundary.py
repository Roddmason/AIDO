from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def make_client(tmp_path: Path) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    token = client.get("/api/v1/security/handshake").json()["token"]
    return store, client, {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_runtime_provider_api_does_not_expose_internal_mock(tmp_path: Path) -> None:
    _store, client, _headers = make_client(tmp_path)

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    payload = response.json()
    assert "internal_mock" not in payload["runtimeModes"]
    assert all(provider["id"] != "internal_mock" for provider in payload["providers"])


def test_model_gateway_does_not_expose_mock_route_execution(tmp_path: Path) -> None:
    _store, client, headers = make_client(tmp_path)

    response = client.post(
        "/api/v1/model-gateway/route/execute-mock",
        headers=headers,
        json={"role": "developer", "taskType": "implementation"},
    )

    assert response.status_code == 404


def test_agent_profile_api_rejects_internal_mock_runtime(tmp_path: Path) -> None:
    _store, client, headers = make_client(tmp_path)

    response = client.post(
        "/api/v1/agent-profiles",
        headers=headers,
        json={
            "id": "bad-internal-runtime",
            "name": "Bad Internal Runtime",
            "role": "implementer",
            "runtimeMode": "internal_mock",
            "permissionProfile": "dev_safe",
        },
    )

    assert response.status_code == 422
    assert "runtime" in str(response.json()["detail"]).lower()


def test_issue_to_patch_rejects_internal_mock_runtime(tmp_path: Path) -> None:
    store, client, headers = make_client(tmp_path)
    project_path = tmp_path / "project"
    project_path.mkdir()
    project = store.create_project(name="Project", path=project_path, template_id="other")

    response = client.post(
        "/api/v1/workflows/issue-to-patch",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Reject internal runtime",
            "issueText": "This must not be simulated.",
            "preferredRuntime": "internal_mock",
            "qaCommands": [["python", "--version"]],
        },
    )

    assert response.status_code == 422
    assert "internal_mock" in response.json()["detail"]


def test_issue_to_pr_rejects_internal_mock_runtime(tmp_path: Path) -> None:
    store, client, headers = make_client(tmp_path)
    project_path = tmp_path / "project-pr"
    project_path.mkdir()
    project = store.create_project(name="Project PR", path=project_path, template_id="other")

    response = client.post(
        "/api/v1/workflows/issue-to-pr",
        headers=headers,
        json={
            "projectId": project["id"],
            "title": "Reject internal runtime",
            "issueText": "This must not be simulated.",
            "preferredRuntime": "internal_mock",
            "qaCommands": [["python", "--version"]],
        },
    )

    assert response.status_code == 422
    assert "internal_mock" in response.json()["detail"]


def test_product_seeds_do_not_create_internal_mock_runtime_records(tmp_path: Path) -> None:
    store, _client, _headers = make_client(tmp_path)
    connection = store.connection

    checks = {
        "agent_profiles": connection.execute(
            "SELECT COUNT(*) FROM agent_profiles WHERE runtime_type = 'internal_mock'"
        ).fetchone()[0],
        "provider_accounts": connection.execute(
            "SELECT COUNT(*) FROM provider_accounts WHERE provider_id = 'internal_mock'"
        ).fetchone()[0],
        "model_catalog": connection.execute(
            "SELECT COUNT(*) FROM model_catalog WHERE provider_id = 'internal_mock'"
        ).fetchone()[0],
        "model_providers": connection.execute(
            "SELECT COUNT(*) FROM model_providers WHERE id = 'internal_mock' OR provider = 'internal_mock'"
        ).fetchone()[0],
        "runtime_capabilities": connection.execute(
            "SELECT COUNT(*) FROM runtime_capabilities WHERE runtime = 'internal_mock'"
        ).fetchone()[0],
    }

    assert checks == {key: 0 for key in checks}
