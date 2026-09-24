"""El product loop solo usa runtimes del equipo sellado del hilo y, por rol, el asignado.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.product_loop import coordinator as coordinator_module
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

TEAM = {
    "runtimeTeam": {
        "allowedRuntimes": ["codex_cli", "nvidia_nim"],
        "roleRuntimes": {"product_owner": "codex_cli", "developer": "codex_cli", "security": "nvidia_nim"},
    }
}
SCHEDULE = {"risk": "medium", "mode": "balanced"}


@pytest.fixture
def coordinator(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield ProductLoopCoordinator(connection, root=tmp_path)


def _capture_selection(monkeypatch) -> list:
    captured: list = []

    def _capture(self, request, *, record=True, allow_decision_inference=False, **kwargs):
        captured.append(request)
        raise RuntimeError("stop after capturing the request")

    monkeypatch.setattr(AIResourceManager, "select_resource", _capture)
    return captured


def _team_request(coordinator, role_plan, request_meta):
    return coordinator._team_resource_request(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=request_meta,
        team_schedule=SCHEDULE,
        role_plan=role_plan,
        agent_tasks=[{"id": "task-1", "role": role_plan["role"]}],
    )


def test_team_roles_are_confined_to_their_assigned_runtime(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    security = {"role": "security_engineer", "kind": "review", "capabilities": ["security_review"]}
    qa = {"role": "qa_engineer", "kind": "review", "capabilities": ["test_design"]}
    assert _team_request(coordinator, build, TEAM).allowed_provider_ids == ["codex_cli"]
    assert _team_request(coordinator, security, TEAM).allowed_provider_ids == ["nvidia_nim"]
    assert _team_request(coordinator, qa, TEAM).allowed_provider_ids == ["codex_cli", "nvidia_nim"]
    assert _team_request(coordinator, build, {}).allowed_provider_ids is None


def test_product_owner_selection_only_offers_the_assigned_runtime(coordinator, monkeypatch):
    captured = _capture_selection(monkeypatch)
    with pytest.raises(RuntimeError, match="stop"):
        coordinator._product_owner_resource_selection(
            project_id="project-team", loop_id="loop-1", task_id="po-task", request_meta=TEAM
        )
    assert captured[0].allowed_provider_ids == ["codex_cli"]
    with pytest.raises(RuntimeError, match="stop"):
        coordinator._product_owner_resource_selection(
            project_id="project-team", loop_id="loop-1", task_id="po-task", request_meta={}
        )
    assert "claude_code_cli" in captured[1].allowed_provider_ids


def test_failover_never_leaves_the_assigned_runtime(coordinator, monkeypatch):
    captured = _capture_selection(monkeypatch)
    run = SimpleNamespace(
        project_id="project-team",
        loop={"id": "loop-1"},
        task_id="task-1",
        team_schedule=SCHEDULE,
        request_meta=TEAM,
    )
    replacement = coordinator._failover_replacement(
        run=run,
        payload={},
        attempts=[{"failureClass": "quota", "providerId": "codex_cli", "model": "gpt-5.5"}],
        provider_id="codex_cli",
        failed_model="gpt-5.5",
        role="developer",
    )
    assert replacement is None
    assert captured[0].allowed_provider_ids == ["codex_cli"]


def _schedule_role(role: str, kind: str, capabilities: list[str], provider_id: str, runtime: str) -> dict:
    return {
        "role": role,
        "kind": kind,
        "capabilities": capabilities,
        "resourceDecision": {"selected": {"providerId": provider_id, "model": "m", "runtime": runtime}},
    }


def test_developer_execution_requires_a_real_decision_for_the_assigned_runtime(coordinator):
    qa_only = {"roles": [_schedule_role("qa_engineer", "review", ["test_design"], "nvidia_nim", "api")]}
    build = {"roles": [_schedule_role("backend_engineer", "build", ["code_edit"], "codex_cli", "cli")]}
    assert coordinator._developer_execution_resource(qa_only, TEAM) == {}
    blocker = coordinator._developer_assignment_blocker(TEAM, {})
    assert blocker is not None and blocker["role"] == "developer" and "codex_cli" in blocker["reason"]
    resource = coordinator._developer_execution_resource(build, TEAM)
    assert (resource["providerId"], resource["preferredRuntime"]) == ("codex_cli", "codex_cli")
    assert coordinator._developer_assignment_blocker(TEAM, resource) is None
    assert coordinator._developer_assignment_blocker({}, {}) is None
    assert coordinator._developer_execution_resource(qa_only)["preferredRuntime"] == "nvidia_nim"


def test_security_model_analysis_only_runs_on_the_assigned_runtime(coordinator):
    def schedule(provider_id: str) -> dict:
        return {
            "roles": [
                {
                    "role": "security_engineer",
                    "kind": "review",
                    "capabilities": ["security_review"],
                    "resourceDecision": {"selected": {"providerId": provider_id, "model": "m"}},
                }
            ]
        }

    assert coordinator._security_execution_resource(schedule("openai_compatible"), TEAM) == {}
    assert coordinator._security_execution_resource(schedule("nvidia_nim"), TEAM) == {
        "preferredRuntime": "nvidia_nim",
        "model": "m",
    }
    assert coordinator._security_execution_resource(schedule("openai_compatible")) == {
        "preferredRuntime": "openai_compatible",
        "model": "m",
    }
    no_security = {
        "runtimeTeam": {"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"developer": "codex_cli"}}
    }
    assert coordinator._security_execution_resource(schedule("nvidia_nim"), no_security) == {}


def _review_run(tmp_path: Path, request_meta: dict) -> SimpleNamespace:
    return SimpleNamespace(
        loop={"id": "loop-1", "context": {}},
        team_schedule={"intent": {"intents": ["architecture"], "risk": "high"}},
        runtime_result={"diffSummary": {"patchArtifactId": "artifact-1"}},
        project_id="project-team",
        workspace={"id": "workspace-1"},
        task_id="task-1",
        rework_round=0,
        resolved_title="Team",
        qa_results=[],
        constitution=None,
        effective_root=tmp_path,
        request_meta=request_meta,
        thread_id=None,
    )


def test_architect_receives_the_assigned_runtime_or_is_skipped(coordinator, tmp_path, monkeypatch):
    payloads: list[dict] = []
    reviews: list[dict] = []

    def fake_run(self, payload):
        payloads.append(payload)
        return {"status": "completed", "verdict": "approved", "reason": "", "evidencePackage": {"id": "ev"}}

    monkeypatch.setattr(coordinator_module.ArchitectAgentRunner, "run", fake_run)
    monkeypatch.setattr(
        coordinator.repository,
        "update_loop_context",
        lambda loop_id, *, context: (
            reviews.append(context["durableRun"]["teamReviews"]) or {"id": loop_id, "context": context}
        ),
    )
    monkeypatch.setattr(coordinator, "_record_loop_event", lambda **kwargs: None)
    with_architect = {
        "runtimeTeam": {
            "allowedRuntimes": ["codex_cli", "nvidia_nim"],
            "roleRuntimes": {
                "developer": "codex_cli",
                "product_owner": "codex_cli",
                "architect": "nvidia_nim",
            },
        }
    }
    coordinator._run_team_review_phase(_review_run(tmp_path, with_architect))
    assert payloads[-1]["preferredRuntime"] == "nvidia_nim"
    coordinator._run_team_review_phase(_review_run(tmp_path, TEAM))
    assert len(payloads) == 1
    assert reviews[-1]["architect"]["status"] == "skipped"
    assert reviews[-1]["architect"]["reason"] == "The thread runtime team assigns no architect runtime."
    discarded_at_seal = {
        **TEAM,
        "runtimeTeamDiscarded": [
            {
                "providerId": "nvidia_nim",
                "status": "stale",
                "reason": "runtime_validation_expired",
                "roles": ["architect"],
            }
        ],
    }
    coordinator._run_team_review_phase(_review_run(tmp_path, discarded_at_seal))
    assert len(payloads) == 1
    assert "nvidia_nim was discarded" in reviews[-1]["architect"]["reason"]
    assert "runtime_validation_expired" in reviews[-1]["architect"]["reason"]
    coordinator._run_team_review_phase(_review_run(tmp_path, {}))
    assert "preferredRuntime" not in payloads[-1]


def test_assigned_developer_ignores_other_roles_on_the_same_runtime(coordinator):
    """Roles no-developer que el schedule ordena antes (aido_lead, architect) no suplantan al developer."""
    lead = _schedule_role("aido_lead", "reason", ["planning"], "codex_cli", "cli")
    lead["resourceDecision"]["selected"]["model"] = "lead-model"
    architect = _schedule_role("architect", "reason", ["architecture"], "codex_cli", "cli")
    architect["resourceDecision"]["selected"]["model"] = "architect-model"
    build = _schedule_role("backend_engineer", "build", ["code_edit"], "codex_cli", "cli")
    build["resourceDecision"]["selected"]["model"] = "build-model"
    resource = coordinator._developer_execution_resource({"roles": [lead, architect, build]}, TEAM)
    assert (resource["role"], resource["model"]) == ("backend_engineer", "build-model")
    assert coordinator._developer_execution_resource({"roles": [lead, architect]}, TEAM) == {}


def _review_run_with_architect_decision(
    tmp_path: Path, request_meta: dict, provider_id: str
) -> SimpleNamespace:
    run = _review_run(tmp_path, request_meta)
    run.team_schedule = {
        **run.team_schedule,
        "roles": [
            {
                "role": "architect",
                "kind": "review",
                "capabilities": ["system_design"],
                "resourceDecision": {"selected": {"providerId": provider_id, "model": "qwen3-coder"}},
            }
        ],
    }
    return run


def test_architect_receives_the_model_selected_by_its_resource_decision(coordinator, tmp_path, monkeypatch):
    ProviderAccountStore(coordinator.connection).upsert_provider_account(
        {
            "providerId": "llama-local",
            "displayName": "llama.cpp local",
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "baseUrl": "http://127.0.0.1:1/v1",
            "enabled": True,
        }
    )
    payloads: list[dict] = []

    def fake_run(self, payload):
        payloads.append(payload)
        return {"status": "completed", "verdict": "approved", "reason": "", "evidencePackage": {"id": "ev"}}

    monkeypatch.setattr(coordinator_module.ArchitectAgentRunner, "run", fake_run)
    monkeypatch.setattr(
        coordinator.repository,
        "update_loop_context",
        lambda loop_id, *, context: {"id": loop_id, "context": context},
    )
    monkeypatch.setattr(coordinator, "_record_loop_event", lambda **kwargs: None)

    coordinator._run_team_review_phase(_review_run_with_architect_decision(tmp_path, {}, "llama-local"))
    assert payloads[-1]["preferredRuntime"] == "llama-local"
    assert payloads[-1]["model"] == "qwen3-coder"

    with_architect = {
        "runtimeTeam": {
            "allowedRuntimes": ["codex_cli", "nvidia_nim"],
            "roleRuntimes": {
                "developer": "codex_cli",
                "product_owner": "codex_cli",
                "architect": "nvidia_nim",
            },
        }
    }
    coordinator._run_team_review_phase(
        _review_run_with_architect_decision(tmp_path, with_architect, "llama-local")
    )
    assert payloads[-1]["preferredRuntime"] == "nvidia_nim"
    assert "model" not in payloads[-1]

    coordinator._run_team_review_phase(_review_run_with_architect_decision(tmp_path, {}, "claude_code_cli"))
    assert "preferredRuntime" not in payloads[-1]
    assert "model" not in payloads[-1]
