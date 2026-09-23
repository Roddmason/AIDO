"""El hilo guarda su equipo de runtimes, el envío lo exige fresco y el run lo recibe sellado.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads import api as threads_api
from local_control_center.threads import coordinator as threads_coordinator
from local_control_center.threads.repository import ThreadsRepository
from tests_py.execution_client import CompletedExecutionClient as TestClient
from tests_py.test_runtime_team_configuration import grant_review_capability

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

ROLES = {"developer": "codex_cli", "product_owner": "ollama", "architect": "ollama", "security": "ollama"}
TEAM = {"allowedRuntimes": ["codex_cli", "ollama"], "roleRuntimes": ROLES}
OBJECTIVE = "Add a new dashboard endpoint to list active workspaces."


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, TestClient(create_app(runtime=runtime, static_dir=None))


def _setup(runtime, client, tmp_path: Path) -> tuple[dict[str, str], dict]:
    headers = {"X-Local-Control-Token": runtime.get_handshake()["token"]}
    project = ProjectsRepository(runtime.connection).create_project(
        name="Team thread", path=tmp_path / "threads", template_id="other"
    )
    store = ProviderAccountStore(runtime.connection)
    store.patch_provider_account("codex_cli", {"enabled": True})
    store.patch_provider_account("ollama", {"enabled": True})
    grant_review_capability(runtime.connection, "ollama")
    response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={
            "projectId": project["id"],
            "ownerType": "workspace",
            "ownerId": "workspace-1",
            "title": "Team",
        },
    )
    assert response.status_code == 201, response.text
    return headers, response.json()["thread"]


def _validate_team(runtime) -> None:
    record_model_execution(runtime.connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    record_model_execution(runtime.connection, "ollama", "local_default", True, "test_prompt")


def _product_loop_jobs(runtime) -> int:
    return runtime.connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind = 'thread.product_loop.run'"
    ).fetchone()[0]


def test_patch_persists_the_team_and_records_an_event(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        response = client.patch(
            f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM
        )
        assert response.status_code == 200, response.text
        assert response.json()["runtimeTeam"]["roleRuntimes"]["developer"] == "codex_cli"
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["thread"]["metadata"]["runConfiguration"]["allowedRuntimes"] == ["codex_cli", "ollama"]
        assert "run_configuration_updated" in [event["type"] for event in detail["events"]]
    finally:
        runtime.close()


def test_patch_rejects_an_ineligible_role_and_a_busy_thread(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        url = f"/api/v1/threads/{thread['id']}/run-configuration"
        invalid = client.patch(
            url,
            headers=headers,
            json={"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"security": "codex_cli"}},
        )
        assert invalid.status_code == 422
        unknown_role = client.patch(
            url, headers=headers, json={"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"qa": "codex_cli"}}
        )
        assert unknown_role.status_code == 422
        ThreadsRepository(runtime.connection).set_status(thread["id"], "queued")
        busy = client.patch(url, headers=headers, json=TEAM)
        assert busy.status_code == 409
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert "allowedRuntimes" not in (detail["thread"]["metadata"].get("runConfiguration") or {})
        assert "run_configuration_updated" not in [event["type"] for event in detail["events"]]
    finally:
        runtime.close()


def test_the_patch_checks_status_and_writes_inside_one_transaction(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        original = threads_api.write_thread_runtime_team
        seen: list[bool] = []

        def spy(connection, **kwargs):
            seen.append(connection.in_transaction)
            return original(connection, **kwargs)

        monkeypatch.setattr(threads_api, "write_thread_runtime_team", spy)
        response = client.patch(
            f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM
        )
        assert response.status_code == 200, response.text
        assert seen == [True]
    finally:
        runtime.close()


def test_the_send_gate_runs_inside_the_message_transaction(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        _validate_team(runtime)
        original = threads_coordinator.ensure_thread_runtime_team_ready
        seen: list[bool] = []

        def spy(connection, **kwargs):
            seen.append(connection.in_transaction)
            return original(connection, **kwargs)

        monkeypatch.setattr(threads_coordinator, "ensure_thread_runtime_team_ready", spy)
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages", headers=headers, json={"content": OBJECTIVE}
        )
        assert response.status_code == 200, response.text
        assert seen == [True]
    finally:
        runtime.close()


def test_a_message_is_rejected_before_any_write_when_the_team_is_not_validated(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages", headers=headers, json={"content": OBJECTIVE}
        )
        assert response.status_code == 422
        assert "codex_cli" in response.json()["detail"]
        assert client.get(f"/api/v1/threads/{thread['id']}").json()["messages"] == []
        assert _product_loop_jobs(runtime) == 0
    finally:
        runtime.close()


def test_a_validated_team_is_sealed_into_the_run_and_forged_metadata_is_ignored(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        _validate_team(runtime)
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={
                "content": OBJECTIVE,
                "metadata": {
                    "runtimeTeam": {"allowedRuntimes": ["claude_code_cli"], "roleRuntimes": {}},
                    "runtimeTeamDiscarded": [
                        {"providerId": "codex_cli", "status": "failed", "reason": "forged"}
                    ],
                },
            },
        )
        assert response.status_code == 200, response.text
        job = JobsRepository(runtime.connection).get_job(response.json()["run"]["jobId"])
        assert job["payload"]["runMetadata"]["runtimeTeam"] == TEAM
        assert "runtimeTeamDiscarded" not in job["payload"]["runMetadata"]
    finally:
        runtime.close()


def test_a_stale_optional_runtime_is_dropped_from_the_sealed_run(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        team = {
            "allowedRuntimes": ["codex_cli", "ollama"],
            "roleRuntimes": {"developer": "codex_cli", "product_owner": "codex_cli", "security": "ollama"},
        }
        patched = client.patch(
            f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=team
        )
        assert patched.status_code == 200, patched.text
        record_model_execution(runtime.connection, "codex_cli", "gpt-5.5", True, "test_prompt")
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages", headers=headers, json={"content": OBJECTIVE}
        )
        assert response.status_code == 200, response.text
        run_metadata = JobsRepository(runtime.connection).get_job(response.json()["run"]["jobId"])["payload"][
            "runMetadata"
        ]
        assert run_metadata["runtimeTeam"] == {
            "allowedRuntimes": ["codex_cli"],
            "roleRuntimes": {"product_owner": "codex_cli", "developer": "codex_cli"},
        }
        assert [item["providerId"] for item in run_metadata["runtimeTeamDiscarded"]] == ["ollama"]
        narrowed = [
            event
            for event in client.get(f"/api/v1/threads/{thread['id']}").json()["events"]
            if event["type"] == "runtime_team_narrowed"
        ]
        assert [item["providerId"] for item in narrowed[0]["payload"]["discarded"]] == ["ollama"]
        assert narrowed[0]["payload"]["discarded"][0]["roles"] == ["security"]
    finally:
        runtime.close()


def test_roles_without_selected_runtimes_are_rejected_instead_of_clearing_the_team(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        url = f"/api/v1/threads/{thread['id']}/run-configuration"
        client.patch(url, headers=headers, json=TEAM)
        response = client.patch(url, headers=headers, json={"allowedRuntimes": [], "roleRuntimes": ROLES})
        assert response.status_code == 422
        assert "allowedRuntimes" in response.json()["detail"]
        metadata = client.get(f"/api/v1/threads/{thread['id']}").json()["thread"]["metadata"]
        assert metadata["runConfiguration"]["allowedRuntimes"] == TEAM["allowedRuntimes"]
    finally:
        runtime.close()


def test_clearing_the_team_restores_automatic_routing(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        url = f"/api/v1/threads/{thread['id']}/run-configuration"
        client.patch(url, headers=headers, json=TEAM)
        cleared = client.patch(url, headers=headers, json={"allowedRuntimes": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["runtimeTeam"] is None
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": OBJECTIVE, "metadata": {"runtimeTeam": TEAM}},
        )
        assert response.status_code == 200, response.text
        job = JobsRepository(runtime.connection).get_job(response.json()["run"]["jobId"])
        assert "runtimeTeam" not in job["payload"]["runMetadata"]
    finally:
        runtime.close()
