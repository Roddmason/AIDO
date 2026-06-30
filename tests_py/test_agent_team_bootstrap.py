from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.team_scheduler.scheduler import schedule_team

BASE_TEAM_ROLES = {
    "aido_lead",
    "product_owner",
    "project_manager",
    "scrum_master",
    "architect",
    "technical_lead",
    "backend_engineer",
    "frontend_engineer",
    "qa_engineer",
    "security_engineer",
    "devops_engineer",
    "researcher",
    "release_manager",
}


def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AIDO_ENABLE_CLI_RUNTIMES",
        "AIDO_ENABLE_REAL_PROVIDER_CALLS",
        "AIDO_OLLAMA_BASE_URL",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "AIDO_CODEX_COMMAND",
        "AIDO_CLAUDE_COMMAND",
        "CODEX_CLI_PATH",
        "CLAUDE_CODE_CLI_PATH",
        "AIDO_OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_API_KEY",
        "AIDO_OPENAI_COMPATIBLE_MODEL",
        "AIDO_OPENROUTER_API_KEY",
        "AIDO_OPENROUTER_MODEL",
        "AIDO_NVIDIA_API_KEY",
        "AIDO_NVIDIA_BASE_URL",
        "AIDO_NVIDIA_MODEL",
        "AIDO_ANTHROPIC_API_KEY",
        "AIDO_ANTHROPIC_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _runtime(tmp_path: Path) -> ControlCenterRuntime:
    return ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")


def _profile_by_role(profiles: list[dict[str, Any]], role: str) -> dict[str, Any]:
    return next(profile for profile in profiles if profile["role"] == role)


def _headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_new_database_bootstraps_base_team_profiles(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        runtime.init()

        profiles = AgentsRepository(runtime.connection).list_agent_profiles()

        assert {profile["role"] for profile in profiles} == BASE_TEAM_ROLES
        assert len(profiles) == len(BASE_TEAM_ROLES)
        for profile in profiles:
            assert profile["status"] == "active"
            assert profile["defaultRuntimePolicy"]["providerCandidates"]
            assert isinstance(profile["allowedTools"], list) and profile["allowedTools"]
            assert isinstance(profile["allowedSkills"], list) and profile["allowedSkills"]
            assert profile["costLimits"]["maxCostPerRun"] == profile["maxCostPerRun"]
            assert profile["costLimits"]["maxTokensPerRun"] == profile["maxTokensPerRun"]
            assert profile["qualityGates"]
            assert profile["reviewerPolicy"]["mode"] in {"none", "peer", "gated", "panel"}
    finally:
        runtime.close()


def test_base_team_bootstrap_is_idempotent(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        runtime.init()
        first = AgentsRepository(runtime.connection).list_agent_profiles()

        runtime.init()
        second = AgentsRepository(runtime.connection).list_agent_profiles()

        assert [profile["id"] for profile in second] == [profile["id"] for profile in first]
        assert len(second) == len(BASE_TEAM_ROLES)
    finally:
        runtime.close()


def test_project_creation_bootstraps_base_team_when_profiles_are_missing(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        client = TestClient(create_app(runtime=runtime, static_dir=None))
        headers = _headers(client)
        runtime.connection.execute("DELETE FROM agent_profiles")

        response = client.post(
            "/api/v1/projects",
            headers=headers,
            json={
                "name": "First Workspace",
                "path": str(tmp_path / "first-workspace"),
                "templateId": "other",
                "createDirectory": True,
            },
        )

        assert response.status_code == 201
        profiles = AgentsRepository(runtime.connection).list_agent_profiles()
        assert {profile["role"] for profile in profiles} == BASE_TEAM_ROLES
    finally:
        runtime.close()


def test_workspace_allocation_bootstraps_base_team_when_profiles_are_missing(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        client = TestClient(create_app(runtime=runtime, static_dir=None))
        headers = _headers(client)
        project = ProjectsRepository(runtime.connection).create_project(
            name="Workspace Allocation",
            path=tmp_path / "workspace-allocation",
            template_id="other",
            create_directory=True,
        )
        runtime.connection.execute("DELETE FROM agent_profiles")

        response = client.post(
            "/api/v1/workspaces",
            headers=headers,
            json={
                "projectId": project["id"],
                "taskId": "task-bootstrap-team",
                "agentId": "operator",
                "reason": "Allocate first isolated workspace.",
                "isolationType": "directory",
            },
        )

        assert response.status_code == 201
        profiles = AgentsRepository(runtime.connection).list_agent_profiles()
        assert {profile["role"] for profile in profiles} == BASE_TEAM_ROLES
    finally:
        runtime.close()


def test_agent_profiles_report_blocked_runtime_instead_of_fake_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_runtime_env(monkeypatch)
    runtime = _runtime(tmp_path)
    try:
        client = TestClient(create_app(runtime=runtime, static_dir=None))
        project = ProjectsRepository(runtime.connection).create_project(
            name="Runtime Blocked",
            path=tmp_path / "runtime-blocked",
            template_id="other",
            create_directory=True,
        )

        response = client.get(f"/api/v1/agent-profiles?projectId={project['id']}")

        assert response.status_code == 200
        profiles = response.json()["agentProfiles"]
        backend = _profile_by_role(profiles, "backend_engineer")
        availability = backend["runtimeAvailability"]
        assert availability["available"] is False
        assert availability["status"] == "blocked"
        assert availability["blockedReason"]
        assert availability["selectedProviderId"] is None
    finally:
        runtime.close()


def test_project_override_changes_effective_profile_without_mutating_base_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_runtime_env(monkeypatch)
    runtime = _runtime(tmp_path)
    try:
        client = TestClient(create_app(runtime=runtime, static_dir=None))
        headers = _headers(client)
        project = ProjectsRepository(runtime.connection).create_project(
            name="Override Project",
            path=tmp_path / "override-project",
            template_id="other",
            create_directory=True,
        )
        base_profiles = client.get("/api/v1/agent-profiles").json()["agentProfiles"]
        backend = _profile_by_role(base_profiles, "backend_engineer")

        response = client.put(
            f"/api/v1/projects/{project['id']}/agent-profile-overrides/{backend['id']}",
            headers=headers,
            json={
                "runtimeMode": "ollama",
                "allowedProviders": ["ollama"],
                "allowedRuntimes": ["ollama"],
                "reason": "Use local runtime for this project when available.",
            },
        )

        assert response.status_code == 200
        effective_profiles = client.get(f"/api/v1/agent-profiles?projectId={project['id']}").json()[
            "agentProfiles"
        ]
        effective_backend = _profile_by_role(effective_profiles, "backend_engineer")
        unchanged_base = _profile_by_role(
            client.get("/api/v1/agent-profiles").json()["agentProfiles"], "backend_engineer"
        )
        assert effective_backend["runtimeMode"] == "ollama"
        assert effective_backend["allowedProviders"] == ["ollama"]
        assert effective_backend["projectOverride"]["projectId"] == project["id"]
        assert (
            effective_backend["projectOverride"]["reason"]
            == "Use local runtime for this project when available."
        )
        assert unchanged_base["runtimeMode"] == backend["runtimeMode"]
        assert unchanged_base["allowedProviders"] == backend["allowedProviders"]
    finally:
        runtime.close()


def test_team_scheduler_activates_subset_of_bootstrapped_roster(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        runtime.init()
        roster_roles = {
            profile["role"] for profile in AgentsRepository(runtime.connection).list_agent_profiles()
        }

        plan = schedule_team(scope=["backend"], risk="low", mode="economy")
        selected_roles = {assignment["role"] for assignment in plan["roles"]}

        assert selected_roles < roster_roles
        assert "backend_engineer" in selected_roles
        assert "frontend_engineer" not in selected_roles
        assert "security_engineer" not in selected_roles
        assert "project_manager" not in selected_roles
    finally:
        runtime.close()
