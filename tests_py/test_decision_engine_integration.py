"""Shadow integration through AIDO selection, ToolBroker and observed outcomes.

Only availability probes, Jev transport and the subprocess boundary are fakes.
Policy, eligible candidates, effective selection and execution audit remain real.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.runtime_registry import (
    build_developer_agent_argv,
    isolated_product_owner_codex_environment,
)
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.decision_engine.models import DecisionResult
from local_control_center.decision_engine.repository import DecisionRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.settings.repository import SettingsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


class FakeJev:
    """A contrary recommendation, without owning any execution dependency."""

    def __init__(self, *, invalid: bool = False, timeout: bool = False):
        self.requests = []
        self.invalid = invalid
        self.timeout = timeout

    async def decide(self, request):
        self.requests.append(request)
        if self.timeout:
            await asyncio.Event().wait()
        ids = [candidate.id for candidate in request.candidates]
        selected = next((value for value in ids if value.startswith("claude_code_cli:")), ids[0])
        ranking = (selected, *(value for value in ids if value != selected))
        probabilities = {value: (0.95 if value == selected else 0.05 / (len(ids) - 1)) for value in ids}
        if len(ids) == 1:
            probabilities[selected] = 1.0
        margin = probabilities[selected] - (probabilities[ranking[1]] if len(ranking) > 1 else 0.0)
        return DecisionResult(
            selected="shell:execute" if self.invalid else selected,
            ranking=ranking,
            probabilities=probabilities,
            confidence=0.95,
            margin=margin,
            engine="jev",
            provider="jev",
            model="jev-1.13.0",
            version="1.13.0",
            decision_type=request.decision_type,
            reason_code="ranked",
        )


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    monkeypatch.setenv("AIDO_ENABLE_REAL_PROVIDER_CALLS", "true")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "synthetic-source"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-local"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    project_path = tmp_path / "shadow-project"
    project_path.mkdir()
    with closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")) as store:
        store.init()
        project = store.create_project(name="Shadow integration", path=project_path, template_id="other")
        connection = store.connection
        connection.execute("UPDATE model_catalog SET enabled = 0")
        connection.execute("UPDATE ai_model_performance SET enabled = 0")
        manager = AIResourceManager(connection)
        statuses = []
        for provider, model in (("codex_cli", "fixture-codex"), ("claude_code_cli", "fixture-claude")):
            manager.upsert_model_performance(
                {
                    "providerId": provider,
                    "model": model,
                    "runtime": "cli",
                    "capabilities": ["chat", "code", "review"],
                    "contextWindow": 128000,
                    "maxOutputTokens": 4096,
                    "inputPricePerMtok": 0.0,
                    "outputPricePerMtok": 0.0,
                    "observedLatencyMs": 100,
                    "observedSuccessRate": 1.0,
                    "reworkRate": 0.0,
                    "qualityScore": 0.9,
                    "locality": "remote",
                    "privacyLevel": "remote_allowed",
                    "evidence": [{"id": "synthetic-selection-prior", "kind": "manual_seed"}],
                }
            )
            statuses.append(
                {
                    "id": provider,
                    "kind": "cli",
                    "configured": True,
                    "available": True,
                    "executable": True,
                    "models": [model],
                    "capabilities": ["chat", "code_edit"],
                }
            )
        monkeypatch.setattr(
            RuntimeStatusService, "list_provider_statuses", lambda _self, *, project_id=None: statuses
        )
        routes = RoutingProfileStore(connection)
        routes.upsert_role_policy(
            {
                "role": "developer",
                "preferred": [{"provider": "codex_cli", "model": "fixture-codex"}],
                "fallback": [{"provider": "claude_code_cli", "model": "fixture-claude"}],
                "allowCli": True,
                "allowRemote": True,
                "allowLocal": False,
                "allowApi": False,
                "allowUnknownCost": False,
                "maxCostPerTaskUsd": 0.0,
            }
        )
        agents = AgentsRepository(connection)
        profile = agents.upsert_agent_profile(
            {
                "id": "developer_agent",
                "name": "Developer Agent",
                "role": "implementer",
                "runtimeMode": "cli",
                "permissionProfile": "dev_safe",
                "allowedTools": ["shell"],
            }
        )
        workspace = WorkspacesRepository(connection, root=tmp_path).allocate_workspace(
            project_id=project["id"],
            task_id="shadow-task",
            agent_id=profile["id"],
        )
        connection.commit()
        yield SimpleNamespace(
            connection=connection,
            project_id=project["id"],
            manager=manager,
            routes=routes,
            agents=agents,
            profile=profile,
            workspace=workspace,
            root=tmp_path,
            coordinator=ProductLoopCoordinator(connection, root=tmp_path),
        )


def _configure(lane, monkeypatch, provider, *, enabled=True, timeout=1.0):
    monkeypatch.setattr("local_control_center.decision_engine.service.real_jev_calls_enabled", lambda: True)
    settings = SettingsRepository(lane.connection)
    settings.set_value("decision_engine.enabled", "project", lane.project_id, enabled)
    settings.set_value("decision_engine.timeout_seconds", "project", lane.project_id, timeout)
    lane.connection.commit()
    monkeypatch.setattr(
        "local_control_center.decision_engine.service.JevDecisionProvider", lambda _config: provider
    )


def _select(lane, *, privacy="remote_allowed"):
    schedule, blockers = lane.coordinator._team_schedule_with_resource_decisions(
        project_id=lane.project_id,
        loop_id="shadow-loop",
        request_meta={"privacyLevel": privacy},
        team_schedule={
            "mode": "balanced",
            "risk": "medium",
            "roles": [
                {
                    "role": "developer",
                    "kind": "build",
                    "capabilities": ["code_edit"],
                    "budgetUsd": 0.0,
                    "maxTokens": 1024,
                }
            ],
        },
        agent_tasks=[{"id": "shadow-task", "role": "developer"}],
    )
    return schedule["roles"][0]["resourceDecision"], blockers


def _execute(lane, monkeypatch, decision, *, deny_shell=False):
    profile = {**lane.profile, "allowedTools": []} if deny_shell else lane.profile
    agent_run = lane.agents.create_agent_run(
        project_id=lane.project_id,
        agent_profile_id=profile["id"],
        task_id="shadow-task",
        input_payload={},
        output_payload={},
        status="running",
    )
    selected = decision["selected"]
    execute = Mock(
        return_value={"stdout": "synthetic success", "stderr": "", "returnCode": 0, "blocked": False}
    )
    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute", execute
    )
    with isolated_product_owner_codex_environment() as environment:
        argv = build_developer_agent_argv(
            runtime={
                "id": selected["providerId"],
                "detectedCommand": str(lane.root / "codex-shadow-fixture.exe"),
            },
            workspace_id=lane.workspace["id"],
            workspace_path=lane.workspace["path"],
            instruction="Synthetic integration fixture",
            qa_commands=[],
            agent_id=profile["id"],
            connection=None,
            model=selected["model"],
        )
        result = ToolBroker(lane.connection, artifact_root=lane.root).evaluate_tool_call(
            project_id=lane.project_id,
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            tool_call={
                "tool": "shell",
                "operation": "developer_agent_runtime",
                "runtimeId": selected["providerId"],
                "capability": "code_edit",
                "argv": argv,
                "execute": True,
                "workspaceId": lane.workspace["id"],
                "workspacePath": lane.workspace["path"],
                "path": lane.workspace["path"],
            },
            trusted_operation="developer_agent_runtime",
            trusted_subprocess_environment=environment,
        )
    return result, execute, argv


def _receipts(lane):
    return DecisionRepository(lane.connection).list_receipts(project_id=lane.project_id)


def test_shadow_recommends_claude_but_real_aido_broker_executes_codex_and_records_outcome(lane, monkeypatch):
    provider = FakeJev()
    _configure(lane, monkeypatch, provider, enabled=False)
    baseline, blockers = _select(lane)
    assert blockers == []
    assert _receipts(lane) == []
    assert provider.requests == []
    _configure(lane, monkeypatch, provider)
    shadow, blockers = _select(lane)
    assert blockers == []
    assert {key: value for key, value in shadow.items() if key != "routingDecisionId"} == {
        key: value for key, value in baseline.items() if key != "routingDecisionId"
    }
    assert shadow["selected"] == baseline["selected"]
    assert shadow["selected"]["providerId"] == "codex_cli"
    before_execution = deepcopy(shadow)
    result, execute, argv = _execute(lane, monkeypatch, shadow)
    assert result["toolCall"]["status"] == "completed"
    execute.assert_called_once()
    assert execute.call_args.kwargs["argv"] == argv
    assert "codex-shadow-fixture.exe" in argv[0]
    assert argv[argv.index("--model") + 1] == "fixture-codex"
    assert shadow == before_execution
    receipt = _receipts(lane)[0]
    assert receipt["effectiveDecision"].startswith("codex_cli:")
    assert receipt["jevRecommendation"].startswith("claude_code_cli:")
    assert receipt["sourceDecisionId"] == shadow["routingDecisionId"]
    assert receipt["reasonCode"] == "recommendation_usable"
    assert receipt["outcome"] is None
    lane.coordinator._record_resource_decision_learning(
        manager=lane.manager,
        role="developer",
        decision=shadow,
        runtime_result={"latencyMs": 12, "actualCostUsd": 0.0},
        usage_entry=None,
        evidence_ref="synthetic-codex-execution",
        success=True,
        rework=False,
        quality_score=0.9,
    )
    outcome = _receipts(lane)[0]["outcome"]
    assert outcome["execution_succeeded"] is True
    assert outcome["evidence_ref"] == "synthetic-codex-execution"


def test_invalid_recommendation_cannot_select_shell_or_bypass_real_tool_broker(lane, monkeypatch):
    _configure(lane, monkeypatch, FakeJev(invalid=True))
    decision, blockers = _select(lane)
    assert blockers == []
    assert decision["selected"]["providerId"] == "codex_cli"
    receipt = _receipts(lane)[0]
    assert receipt["fallbackReason"] == "invalid_choice"
    assert "shell:execute" not in receipt["candidates"]
    result, execute, _ = _execute(lane, monkeypatch, decision, deny_shell=True)
    assert result["toolCall"]["status"] == "denied"
    execute.assert_not_called()


def test_explicit_preference_cannot_resurrect_a_policy_blocked_runtime(lane, monkeypatch):
    provider = FakeJev()
    _configure(lane, monkeypatch, provider)
    policy = lane.routes.get_role_policy("developer")
    lane.routes.upsert_role_policy({**policy, "blocked": [{"provider": "codex_cli", "model": "*"}]})
    lane.connection.commit()
    decision, blockers = _select(lane)
    assert blockers == []
    assert decision["selected"]["providerId"] == "claude_code_cli"
    assert any(
        item["providerId"] == "codex_cli" and item["reason"] == "role_blocks_candidate"
        for item in decision["rejected"]
    )
    assert len(provider.requests) == 1
    assert all(not candidate.id.startswith("codex_cli:") for candidate in provider.requests[0].candidates)


def test_ineligible_explicit_cli_preference_leaves_aido_blocked_without_jev(lane, monkeypatch):
    provider = FakeJev()
    _configure(lane, monkeypatch, provider)
    policy = lane.routes.get_role_policy("developer")
    lane.routes.upsert_role_policy({**policy, "allowCli": False})
    lane.connection.commit()
    decision, blockers = _select(lane)
    assert decision["selected"] is None
    assert decision["candidates"] == []
    assert blockers
    assert {item["reason"] for item in decision["rejected"]} == {"role_blocks_cli"}
    assert provider.requests == []
    assert _receipts(lane)[0]["fallbackReason"] == "no_eligible_candidates"


def test_local_only_resource_observation_never_calls_jev(lane, monkeypatch):
    from local_control_center.decision_engine.observers import observe_resource_decision

    provider = FakeJev()
    _configure(lane, monkeypatch, provider, enabled=False)
    decision, _ = _select(lane)
    _configure(lane, monkeypatch, provider)
    original = deepcopy(decision)
    observe_resource_decision(
        lane.connection,
        decision=decision,
        project_id=lane.project_id,
        execution_id="shadow-loop",
        task_id="shadow-task",
        privacy_mode="local_only",
    )
    assert decision == original
    assert provider.requests == []
    assert _receipts(lane)[0]["fallbackReason"] == "privacy_blocked"


def test_timeout_keeps_selection_without_processes_or_resource_reservations(lane, monkeypatch):
    provider = FakeJev(timeout=True)
    _configure(lane, monkeypatch, provider, timeout=0.01)
    tables = ("provider_execution_leases", "resource_leases", "agent_tool_calls", "jobs")
    before = {
        table: lane.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
    }
    process = Mock(side_effect=AssertionError("Shadow must not create a process"))
    monkeypatch.setattr("subprocess.Popen", process)
    decision, blockers = _select(lane)
    after = {
        table: lane.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
    }
    assert blockers == []
    assert decision["selected"]["providerId"] == "codex_cli"
    assert before == after
    process.assert_not_called()
    assert _receipts(lane)[0]["fallbackReason"] == "timeout"
