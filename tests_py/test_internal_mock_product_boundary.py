from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


@pytest.fixture
def mock_boundary_client(tmp_path: Path) -> Iterator[tuple[ControlPlaneFixture, TestClient, dict[str, str]]]:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    try:
        store.init()
        app = create_app(runtime=store, static_dir=None)
        with TestClient(app) as client:
            token = client.get("/api/v1/security/handshake").json()["token"]
            yield store, client, {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}
    finally:
        store.close()


def test_runtime_provider_api_does_not_expose_internal_mock(mock_boundary_client) -> None:
    _store, client, _headers = mock_boundary_client

    response = client.get("/api/v1/runtime/providers")

    assert response.status_code == 200
    payload = response.json()
    assert "internal_mock" not in payload["runtimeModes"]
    assert all(provider["id"] != "internal_mock" for provider in payload["providers"])


def test_model_gateway_does_not_expose_mock_route_execution(mock_boundary_client) -> None:
    _store, client, headers = mock_boundary_client

    response = client.post(
        "/api/v1/model-gateway/route/execute-mock",
        headers=headers,
        json={"role": "developer", "taskType": "implementation"},
    )

    assert response.status_code == 404


def test_agent_profile_api_rejects_internal_mock_runtime(mock_boundary_client) -> None:
    _store, client, headers = mock_boundary_client

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


def test_issue_to_patch_rejects_internal_mock_runtime(tmp_path: Path, mock_boundary_client) -> None:
    store, client, headers = mock_boundary_client
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
    assert "not in the product catalog" in str(response.json()["detail"])
    # Acotado al proyecto de la peticion rechazada: es la propiedad que importa, y es mas precisa
    # que el conteo global, que ademas cuenta el refresco de salud del arranque (project_id NULL).
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM operational_executions WHERE project_id = ?", (project["id"],)
        ).fetchone()[0]
        == 0
    )


def test_issue_to_pr_rejects_internal_mock_runtime(tmp_path: Path, mock_boundary_client) -> None:
    store, client, headers = mock_boundary_client
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
    assert "not in the product catalog" in str(response.json()["detail"])
    # Acotado al proyecto de la peticion rechazada: es la propiedad que importa, y es mas precisa
    # que el conteo global, que ademas cuenta el refresco de salud del arranque (project_id NULL).
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM operational_executions WHERE project_id = ?", (project["id"],)
        ).fetchone()[0]
        == 0
    )


def test_product_seeds_do_not_create_internal_mock_runtime_records(mock_boundary_client) -> None:
    store, _client, _headers = mock_boundary_client
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

    assert checks == dict.fromkeys(checks, 0)
