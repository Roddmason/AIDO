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


def test_runtime_team_reports_the_global_assignment_sources_and_active_providers(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        SettingsRepository(runtime.connection).set_value(
            "team.role.developer", "project", project_id, ["codex_cli", "ghost"]
        )
        response = client.get("/api/v1/runtime/team", params={"projectId": project_id})
        assert response.status_code == 200, response.text
        body = response.json()
        roles = {item["role"]: item for item in body["roles"]}
        assert roles["developer"]["source"] == "project"
        assert roles["developer"]["effective"] == ["codex_cli"]
        assert roles["developer"]["assigned"] == "codex_cli"
        assert roles["developer"]["invalid"] == ["ghost"]
        assert roles["developer"]["required"] is True
        assert roles["product_owner"]["source"] == "automatic"
        assert roles["technical_lead"]["source"] == "inherited"
        assert roles["architect"]["required"] is False
        assert body["activeProviders"] >= 2
        assert {item["providerId"] for item in body["candidates"]} >= {"codex_cli", "ollama"}
        general = client.get("/api/v1/runtime/team")
        assert general.status_code == 200, general.text
        assert {item["role"]: item for item in general.json()["roles"]}["developer"]["source"] == "automatic"
    finally:
        runtime.close()


def test_runtime_providers_report_the_operator_switch(tmp_path: Path) -> None:
    """Un solo GET por runtime: el estado de proveedores puede cachearse entre llamadas."""
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        ProviderAccountStore(runtime.connection).patch_provider_account("ollama", {"enabled": False})
        providers = client.get("/api/v1/runtime/providers", params={"projectId": project_id}).json()[
            "providers"
        ]
        by_id = {item["id"]: item for item in providers}
        assert by_id["codex_cli"]["enabled"] is True
        assert by_id["ollama"]["enabled"] is False
    finally:
        runtime.close()


def test_the_status_bar_rule_and_the_team_endpoint_count_the_same_active_providers(tmp_path: Path) -> None:
    """La barra de estado cuenta ``enabled && policyAllowed && kind != manual`` sobre /runtime/providers
    (``shellStatus.ts:isActiveTeamProvider``); debe coincidir con ``activeProviders`` del equipo."""
    runtime, client = _client(tmp_path)
    try:
        _prepare(runtime, tmp_path)
        ProviderAccountStore(runtime.connection).patch_provider_account("manual", {"enabled": True})
        providers = client.get("/api/v1/runtime/providers").json()["providers"]
        status_bar = [
            item["id"]
            for item in providers
            if item.get("enabled")
            and item.get("policyAllowed") is not False
            and item["kind"] in {"cli", "api", "gateway", "local"}
        ]
        team = client.get("/api/v1/runtime/team").json()
        assert "manual" not in status_bar
        assert team["activeProviders"] == len(status_bar)
    finally:
        runtime.close()


def test_a_provider_with_a_live_execution_lease_is_reported_in_use(tmp_path: Path) -> None:
    """El switch no deja apagar un proveedor mientras una llamada suya sigue en curso."""
    from datetime import UTC, datetime, timedelta

    runtime, client = _client(tmp_path)
    try:
        _prepare(runtime, tmp_path)
        now = datetime.now(UTC)
        for lease_id, provider, state, expires in (
            ("lease-live", "codex_cli", "dispatched", now + timedelta(minutes=5)),
            ("lease-expired", "ollama", "active", now - timedelta(minutes=5)),
        ):
            runtime.connection.execute(
                """INSERT INTO provider_execution_leases
                   (id, provider_id, model, state, expires_at, created_at, updated_at)
                   VALUES (?, ?, 'm', ?, ?, ?, ?)""",
                (lease_id, provider, state, expires.isoformat(), now.isoformat(), now.isoformat()),
            )
        providers = client.get("/api/v1/runtime/providers").json()["providers"]
        by_id = {item["id"]: item for item in providers}
        assert by_id["codex_cli"]["inUse"] is True
        assert by_id["ollama"]["inUse"] is False
    finally:
        runtime.close()
