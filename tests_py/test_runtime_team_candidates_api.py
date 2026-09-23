"""Candidatos del equipo de runtimes: validación reciente, roles elegibles y reparto sugerido.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.repository import SettingsRepository
from tests_py.execution_client import CompletedExecutionClient as TestClient

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, TestClient(create_app(runtime=runtime, static_dir=None))


def _prepare(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Team candidates", path=tmp_path / "project", template_id="other"
    )
    store = ProviderAccountStore(runtime.connection)
    store.patch_provider_account("codex_cli", {"enabled": True})
    store.patch_provider_account("ollama", {"enabled": True})
    record_model_execution(runtime.connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    return project["id"]


def test_candidates_report_validation_roles_and_the_backend_split(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        response = client.get("/api/v1/runtime/team-candidates", params={"projectId": project_id})
        assert response.status_code == 200, response.text
        body = response.json()
        by_id = {item["providerId"]: item for item in body["candidates"]}
        assert by_id["codex_cli"]["validation"]["status"] == "validated"
        assert by_id["codex_cli"]["eligibleRoles"] == ["product_owner", "developer"]
        assert by_id["codex_cli"]["kind"] == "cli"
        assert by_id["ollama"]["validation"]["status"] == "never"
        assert "manual" not in by_id
        assert body["freshnessSeconds"] == 1800
        assert body["suggestedRoleRuntimes"]["developer"] == "codex_cli"
        assert body["suggestedRoleRuntimes"]["product_owner"] == "codex_cli"
        assert body["suggestedRoleRuntimes"]["architect"] is None
    finally:
        runtime.close()


def test_the_split_only_uses_selected_and_validated_runtimes(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        response = client.get(
            "/api/v1/runtime/team-candidates", params={"projectId": project_id, "selected": "ollama"}
        )
        assert response.status_code == 200, response.text
        assert set(response.json()["suggestedRoleRuntimes"].values()) == {None}
        empty = client.get(
            "/api/v1/runtime/team-candidates", params={"projectId": project_id, "selected": ""}
        )
        assert set(empty.json()["suggestedRoleRuntimes"].values()) == {None}
    finally:
        runtime.close()


def test_a_runtime_denied_by_the_project_policy_is_flagged_and_never_suggested(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        SettingsRepository(runtime.connection).set_value(
            "project.runtime.allowedProviders", "project", project_id, ["ollama"]
        )
        response = client.get("/api/v1/runtime/team-candidates", params={"projectId": project_id})
        assert response.status_code == 200, response.text
        body = response.json()
        codex = next(item for item in body["candidates"] if item["providerId"] == "codex_cli")
        assert codex["validation"]["status"] == "policy_denied"
        assert "allowedProviders" in codex["validation"]["reason"]
        assert set(body["suggestedRoleRuntimes"].values()) == {None}
    finally:
        runtime.close()
