"""roleModels se sella en el servidor, fija el modelo por rol en el loop y el cambio de modelo queda visible.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.model_execution_health import VALIDATION_TTL_SECONDS, record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.product_loop import coordinator as coordinator_module
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_team.configuration import (
    ROLE_MODEL_CAPABILITIES,
    ROLE_MODELS_KEY,
    RUNTIME_TEAM_METADATA_KEY,
    assess_runtime_team,
    ensure_thread_runtime_team_ready,
    read_thread_runtime_team,
    resolve_team_role_models,
    role_model_pins,
    runtime_team_of,
    seal_thread_runtime_team,
    write_thread_runtime_team,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.threads.repository import ThreadsRepository


class _States:
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states

    def get(self, account, *, max_wait_s: float = 1.0) -> dict[str, str]:
        return dict(self.states)


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        local_model_state, "LOAD_STATE_CACHE", _States({"gemma-a": "loaded", "qwen-b": "unloaded"})
    )
    statuses = [
        {
            "id": "llama_cpp",
            "kind": "local",
            "providerFamily": "openai_compatible",
            "configured": True,
            "available": True,
            "executable": True,
            "capabilities": ["chat"],
            "models": ["gemma-a", "qwen-b"],
            "reason": "Controlled local endpoint.",
        }
    ]
    monkeypatch.setattr(
        RuntimeStatusService, "list_provider_statuses", lambda _service, *, project_id=None: statuses
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Local team", path=tmp_path / "project", template_id="other"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Team"
        )
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "llama_cpp",
                "displayName": "llama.cpp",
                "providerType": "local",
                "apiFormat": "openai_compatible",
                "providerFamily": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        store.set_provider_catalog_id("llama_cpp", "llama_cpp")
        for model in ("gemma-a", "qwen-b"):
            store.upsert_model({"providerId": "llama_cpp", "model": model, "enabled": True, "source": "test"})
        settings = LocalModelSettingsRepository(connection)
        settings.upsert("llama_cpp", "gemma-a", actor="operator", is_default=True)
        settings.upsert("llama_cpp", "qwen-b", actor="operator", code_edit=True)
        record_model_execution(connection, "llama_cpp", "gemma-a", True, "test_prompt", started_at=_ago(3))
        record_model_execution(connection, "llama_cpp", "qwen-b", True, "test_prompt", started_at=_ago(2))
        write_thread_runtime_team(
            connection,
            thread_id=thread["id"],
            project_id=project["id"],
            allowed_runtimes=["llama_cpp"],
            role_runtimes={"product_owner": "llama_cpp", "developer": "llama_cpp"},
        )
        yield connection, project, thread


def _seal(connection, project, thread, metadata=None):
    return seal_thread_runtime_team(
        connection, project_id=project["id"], thread_id=thread["id"], metadata=metadata or {}
    )


def test_sealing_resolves_one_model_per_role_and_drops_a_forged_value(lane):
    connection, project, thread = lane
    forged = {
        RUNTIME_TEAM_METADATA_KEY: {
            "allowedRuntimes": ["llama_cpp"],
            "roleRuntimes": {"product_owner": "llama_cpp"},
            ROLE_MODELS_KEY: {"product_owner": "forged"},
        }
    }
    sealed = _seal(connection, project, thread, forged)
    assert sealed[RUNTIME_TEAM_METADATA_KEY][ROLE_MODELS_KEY] == {
        "product_owner": "gemma-a",
        "developer": "qwen-b",
    }


def test_role_requirements_share_the_model_capability_vocabulary():
    assert ROLE_MODEL_CAPABILITIES["developer"] == frozenset({"chat", "code_edit"})
    assert ROLE_MODEL_CAPABILITIES["security"] == frozenset({"chat", "code_review"})
    assert (
        ROLE_MODEL_CAPABILITIES["product_owner"]
        == ROLE_MODEL_CAPABILITIES["architect"]
        == frozenset({"chat"})
    )


def test_role_models_saved_in_the_thread_configuration_are_ignored(lane):
    connection, project, thread = lane
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread["id"],)).fetchone()
    metadata = json_loads(row["metadata"], {})
    metadata["runConfiguration"][ROLE_MODELS_KEY] = {"product_owner": "qwen-b", "developer": "gemma-a"}
    connection.execute(
        "UPDATE project_threads SET metadata = ? WHERE id = ?", (json_dumps(metadata), thread["id"])
    )
    assert ROLE_MODELS_KEY not in read_thread_runtime_team(connection, thread["id"])
    sealed = _seal(connection, project, thread)
    assert sealed[RUNTIME_TEAM_METADATA_KEY][ROLE_MODELS_KEY] == {
        "product_owner": "gemma-a",
        "developer": "qwen-b",
    }


def test_the_execution_gate_reads_the_sealed_model_of_each_role(lane):
    connection, project, thread = lane
    team = runtime_team_of(_seal(connection, project, thread))
    assert assess_runtime_team(
        connection, team, max_age_seconds=VALIDATION_TTL_SECONDS, only_assigned=True
    ).ready
    record_model_execution(connection, "llama_cpp", "qwen-b", False, "tool_broker", started_at=_ago(1))
    readiness = assess_runtime_team(
        connection, team, max_age_seconds=VALIDATION_TTL_SECONDS, only_assigned=True
    )
    assert [(item["providerId"], item.get("model")) for item in readiness.stale_runtimes] == [
        ("llama_cpp", "qwen-b")
    ]


def test_a_later_failure_of_another_model_keeps_the_runtime_of_a_validated_role_model(lane):
    connection, project, thread = lane
    record_model_execution(connection, "llama_cpp", "qwen-b", False, "test_prompt", started_at=_ago(1))
    ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    team = _seal(connection, project, thread)[RUNTIME_TEAM_METADATA_KEY]
    assert team["allowedRuntimes"] == ["llama_cpp"]
    assert team["roleRuntimes"]["product_owner"] == "llama_cpp"
    assert team[ROLE_MODELS_KEY] == {"product_owner": "gemma-a"}


def test_role_models_accept_the_caller_load_states_for_the_candidates_preview(lane):
    connection, _project, _thread = lane
    roles = {"product_owner": "llama_cpp", "developer": "llama_cpp", "architect": None}
    assert resolve_team_role_models(connection, roles) == {"product_owner": "gemma-a", "developer": "qwen-b"}
    preview = resolve_team_role_models(
        connection, roles, load_states_for=lambda account: {"qwen-b": "loaded"}
    )
    assert preview == {"product_owner": "qwen-b", "developer": "qwen-b"}


def test_role_model_pins_follow_the_sealed_team(lane):
    connection, project, thread = lane
    sealed = _seal(connection, project, thread)
    assert role_model_pins(sealed, "developer") == {"llama_cpp": "qwen-b"}
    assert role_model_pins(sealed, "product_owner") == {"llama_cpp": "gemma-a"}
    assert role_model_pins(sealed, None) == {}
    assert role_model_pins(sealed, "architect") == {}
    assert role_model_pins({}, "developer") == {}


def test_a_role_without_its_own_assignment_is_not_pinned_to_the_product_owner_model(lane):
    connection, project, thread = lane
    ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())
    store = ProviderAccountStore(connection)
    for model in ("gemma-a", "qwen-b"):
        store.upsert_model(
            {
                "providerId": "llama_cpp",
                "model": model,
                "enabled": True,
                "freeTier": True,
                "inputPricePerMtok": 0,
                "outputPricePerMtok": 0,
                "source": "test",
            }
        )
    LocalModelSettingsRepository(connection).upsert("llama_cpp", "qwen-b", actor="operator", code_review=True)
    sealed = _seal(connection, project, thread)
    coordinator = ProductLoopCoordinator(connection, root=project["path"])
    request = coordinator._team_resource_request(
        project_id=project["id"],
        loop_id="loop-1",
        request_meta=sealed,
        team_schedule={"risk": "medium", "mode": "balanced"},
        role_plan={"role": "qa_engineer", "kind": "review", "capabilities": ["test_design"]},
        agent_tasks=[{"id": "task-1", "role": "qa_engineer"}],
    )
    assert request.local_model_pins == {}
    decision = AIResourceManager(connection).select_resource(request, record=False)
    assert (decision.get("selected") or {}).get("model") == "qwen-b"


def test_team_and_product_owner_requests_carry_the_sealed_pins(lane, monkeypatch):
    connection, project, thread = lane
    sealed = _seal(connection, project, thread)
    coordinator = ProductLoopCoordinator(connection, root=project["path"])
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    request = coordinator._team_resource_request(
        project_id=project["id"],
        loop_id="loop-1",
        request_meta=sealed,
        team_schedule={"risk": "medium", "mode": "balanced"},
        role_plan=build,
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
    )
    assert request.local_model_pins == {"llama_cpp": "qwen-b"}
    captured: list = []

    def _capture(self, request, **kwargs):
        captured.append(request)
        raise RuntimeError("stop after capturing the request")

    monkeypatch.setattr(AIResourceManager, "select_resource", _capture)
    with pytest.raises(RuntimeError, match="stop"):
        coordinator._product_owner_resource_selection(
            project_id=project["id"], loop_id="loop-1", task_id="po-task", request_meta=sealed
        )
    assert captured[0].local_model_pins == {"llama_cpp": "gemma-a"}


def test_a_later_role_prefers_the_local_model_an_earlier_role_resolved(lane, monkeypatch):
    connection, project, _thread = lane
    coordinator = ProductLoopCoordinator(connection, root=project["path"])
    captured: list = []

    def _select(self, request, **kwargs):
        captured.append(request)
        return {
            "selected": {"providerId": "llama_cpp", "model": "gemma-a"},
            "approvalRequired": False,
            "policyResult": {
                "localModelSelections": [
                    {
                        "runtimeId": "llama_cpp",
                        "model": "gemma-a",
                        "reason": "loaded",
                        "requiresSwitch": False,
                        "fromModel": None,
                    }
                ]
            },
        }

    monkeypatch.setattr(AIResourceManager, "select_resource", _select)
    monkeypatch.setattr(coordinator_module, "observe_resource_decision", lambda *args, **kwargs: None)
    roles = [
        {"role": "product_manager", "kind": "reason", "capabilities": []},
        {"role": "qa_engineer", "kind": "review", "capabilities": ["test_design"]},
    ]
    coordinator._team_schedule_with_resource_decisions(
        project_id=project["id"],
        loop_id="loop-1",
        request_meta={},
        team_schedule={"risk": "medium", "mode": "balanced", "roles": roles},
        agent_tasks=[{"id": "t1", "role": "product_manager"}, {"id": "t2", "role": "qa_engineer"}],
    )
    assert [request.local_model_affinity for request in captured] == [{}, {"llama_cpp": "gemma-a"}]


def test_a_model_switch_is_recorded_on_the_thread_and_in_the_execution_audit(lane):
    connection, project, thread = lane
    coordinator = ProductLoopCoordinator(connection, root=project["path"])
    decision = {
        "selected": {"providerId": "llama_cpp", "model": "qwen-b"},
        "policyResult": {
            "localModelSelections": [
                {
                    "runtimeId": "llama_cpp",
                    "model": "qwen-b",
                    "reason": "default",
                    "requiresSwitch": True,
                    "fromModel": "gemma-a",
                }
            ]
        },
    }
    coordinator._record_local_model_switches(
        project_id=project["id"],
        loop_id="loop-1",
        thread_id=thread["id"],
        role="developer",
        decision=decision,
    )
    coordinator._record_local_model_switches(
        project_id=project["id"],
        loop_id="loop-1",
        thread_id=thread["id"],
        role="developer",
        decision={**decision, "selected": {"providerId": "codex_cli", "model": "gpt-5.5"}},
    )
    events = [
        event
        for event in ThreadsRepository(connection).list_events(thread["id"])
        if event["type"] == "local_model_switch"
    ]
    assert [event["payload"] for event in events] == [
        {
            "loopId": "loop-1",
            "runtimeId": "llama_cpp",
            "fromModel": "gemma-a",
            "toModel": "qwen-b",
            "role": "developer",
            "reason": "default",
        }
    ]
    audit = connection.execute(
        "SELECT COUNT(*) FROM events WHERE project_id = ? AND type = 'product_loop.local_model_switch'",
        (project["id"],),
    ).fetchone()[0]
    assert audit == 1


def test_a_failover_to_a_model_that_is_not_loaded_records_the_switch(lane, monkeypatch):
    connection, project, thread = lane
    coordinator = ProductLoopCoordinator(connection, root=project["path"])
    decision = {
        "selected": {"providerId": "llama_cpp", "model": "qwen-b"},
        "approvalRequired": False,
        "estimatedCostUsd": 0.0,
        "costTier": "local",
        "policyResult": {
            "localModelSelections": [
                {
                    "runtimeId": "llama_cpp",
                    "model": "qwen-b",
                    "reason": "operator_order",
                    "requiresSwitch": True,
                    "fromModel": "gemma-a",
                }
            ]
        },
    }
    monkeypatch.setattr(AIResourceManager, "select_resource", lambda _self, _request, **_kwargs: decision)
    run = coordinator_module._UserMessageRun(
        project_id=project["id"],
        message="Fix the export",
        actor="operator",
        thread_id=thread["id"],
        loop={"id": "loop-1"},
        task_id="task-1",
    )
    replacement = coordinator._failover_replacement(
        run=run,
        payload={"taskId": "task-1", "model": "gpt-5.5"},
        attempts=[{"failureClass": "transport", "providerId": "codex_cli", "model": "gpt-5.5"}],
        provider_id="codex_cli",
        failed_model="gpt-5.5",
        role="developer",
    )
    assert replacement is not None and replacement["model"] == "qwen-b"
    events = [
        event
        for event in ThreadsRepository(connection).list_events(thread["id"])
        if event["type"] == "local_model_switch"
    ]
    switches = [(event["payload"]["fromModel"], event["payload"]["toModel"]) for event in events]
    assert switches == [("gemma-a", "qwen-b")]


def test_a_product_owner_failover_retries_the_other_model_of_its_pinned_runtime(lane):
    connection, project, thread = lane
    ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())
    store = ProviderAccountStore(connection)
    for model in ("gemma-a", "qwen-b"):
        store.upsert_model(
            {
                "providerId": "llama_cpp",
                "model": model,
                "enabled": True,
                "freeTier": True,
                "inputPricePerMtok": 0,
                "outputPricePerMtok": 0,
                "source": "test",
            }
        )
    sealed = _seal(connection, project, thread)
    assert role_model_pins(sealed, "product_owner") == {"llama_cpp": "gemma-a"}
    coordinator = ProductLoopCoordinator(connection, root=project["path"])
    run = coordinator_module._UserMessageRun(
        project_id=project["id"],
        message="Fix the export",
        actor="operator",
        thread_id=thread["id"],
        loop={"id": "loop-1"},
        task_id="task-1",
    )
    run.request_meta = sealed
    replacement = coordinator._failover_replacement(
        run=run,
        payload={"taskId": "po-task", "model": "gemma-a"},
        attempts=[{"failureClass": "transport", "providerId": "llama_cpp", "model": "gemma-a"}],
        provider_id="llama_cpp",
        failed_model="gemma-a",
        role="product_owner",
    )
    assert replacement is not None
    assert (replacement["preferredRuntime"], replacement["model"]) == ("llama_cpp", "qwen-b")
