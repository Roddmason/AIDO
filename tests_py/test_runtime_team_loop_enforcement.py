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


def _team_request(coordinator, role_plan, request_meta, *, product_owner_provider_id=None):
    return coordinator._team_resource_request(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=request_meta,
        team_schedule=SCHEDULE,
        role_plan=role_plan,
        agent_tasks=[{"id": "task-1", "role": role_plan["role"]}],
        product_owner_provider_id=product_owner_provider_id,
    )


def _fake_select_resource(responses: dict):
    """Responde por ``allowed_provider_ids`` (tupla o ``None``) sin tocar Jev ni el catálogo real."""
    calls: list = []

    def _select(self, request, *, record=True, allow_decision_inference=False, **kwargs):
        calls.append(request)
        key = tuple(request.allowed_provider_ids) if request.allowed_provider_ids else None
        response = responses[key]
        return {
            "routingDecisionId": f"routing-{len(calls)}",
            "selected": response.get("selected"),
            "candidates": response.get("candidates", []),
            "rejected": [],
            "decisionReason": response.get("decisionReason", ""),
            "policyResult": {"localModelSelections": response.get("localModelSelections", [])},
            "approvalRequired": False,
        }

    return calls, _select


def test_team_roles_are_confined_to_their_assigned_runtime(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    security = {"role": "security_engineer", "kind": "review", "capabilities": ["security_review"]}
    qa = {"role": "qa_engineer", "kind": "review", "capabilities": ["test_design"]}
    assert _team_request(coordinator, build, TEAM).allowed_provider_ids == ["codex_cli"]
    assert _team_request(coordinator, security, TEAM).allowed_provider_ids == ["nvidia_nim"]
    assert _team_request(coordinator, qa, TEAM).allowed_provider_ids == ["codex_cli"]
    assert _team_request(coordinator, build, {}).allowed_provider_ids is None


def test_thread_without_a_team_confines_the_role_to_the_product_owners_runtime(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    request = _team_request(coordinator, build, {}, product_owner_provider_id="llama_cpp")
    assert request.allowed_provider_ids == ["llama_cpp"]
    # Con equipo, la herencia del PO no debe alterar la asignación real del rol.
    assert _team_request(
        coordinator, build, TEAM, product_owner_provider_id="llama_cpp"
    ).allowed_provider_ids == ["codex_cli"]


def test_thread_without_a_team_completes_selection_with_a_single_po_candidate(coordinator, monkeypatch):
    """Con un solo candidato dentro del proveedor del PO, la selección se completa sin bloqueo."""
    role_plan = {"role": "aido_lead", "kind": "reason", "capabilities": ["planning"]}
    schedule = {**SCHEDULE, "roles": [role_plan]}
    responses = {
        ("llama_cpp",): {
            "selected": {"providerId": "llama_cpp", "model": "gemma-4-26b-a4b", "runtime": "local"},
            "candidates": [{"providerId": "llama_cpp"}],
            "localModelSelections": [{"runtimeId": "llama_cpp", "model": "gemma-4-26b-a4b"}],
        }
    }
    calls, fake = _fake_select_resource(responses)
    monkeypatch.setattr(AIResourceManager, "select_resource", fake)
    enriched, blockers = coordinator._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta={},
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "aido_lead"}],
        product_owner_selected_resource={"providerId": "llama_cpp", "model": "gemma-4-26b-a4b"},
    )
    assert blockers == []
    assert len(calls) == 1
    assert calls[0].allowed_provider_ids == ["llama_cpp"]
    assert calls[0].local_model_affinity == {"llama_cpp": "gemma-4-26b-a4b"}
    assert enriched["roles"][0]["resourceDecision"]["selected"]["providerId"] == "llama_cpp"


def test_role_with_no_candidates_under_the_po_provider_widens_to_the_full_set(coordinator, monkeypatch):
    """Herencia blanda: capacidades/política descartan al proveedor del PO -> se reintenta sin restringir."""
    role_plan = {"role": "security_engineer", "kind": "review", "capabilities": ["security_review"]}
    schedule = {**SCHEDULE, "roles": [role_plan]}
    responses = {
        ("llama_cpp",): {"selected": None, "candidates": []},
        None: {
            "selected": {"providerId": "codex_cli", "model": "gpt-5.5", "runtime": "cli"},
            "candidates": [{"providerId": "codex_cli"}],
        },
    }
    calls, fake = _fake_select_resource(responses)
    monkeypatch.setattr(AIResourceManager, "select_resource", fake)
    events: list = []
    monkeypatch.setattr(coordinator, "_record_loop_event", lambda **kwargs: events.append(kwargs))
    enriched, blockers = coordinator._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta={},
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "security_engineer"}],
        product_owner_selected_resource={"providerId": "llama_cpp", "model": "gemma-4-26b-a4b"},
    )
    assert blockers == []
    assert [call.allowed_provider_ids for call in calls] == [["llama_cpp"], None]
    assert enriched["roles"][0]["resourceDecision"]["selected"]["providerId"] == "codex_cli"
    assert len(events) == 1
    assert events[0]["event_type"] == "product_loop.runtime_allowlist_widened"
    assert events[0]["payload"]["role"] == "security_engineer"


def test_confidence_below_threshold_is_not_widened(coordinator, monkeypatch):
    """Jev bloqueado por baja confianza (2+ candidatos) no debe ampliarse: el diseño respeta que no
    hay respaldo determinista cuando Jev no está seguro."""
    role_plan = {"role": "aido_lead", "kind": "reason", "capabilities": ["planning"]}
    schedule = {**SCHEDULE, "roles": [role_plan]}
    responses = {
        ("llama_cpp",): {
            "selected": None,
            "candidates": [{"providerId": "llama_cpp"}, {"providerId": "gemini"}],
            "decisionReason": "Jev runtime selection blocked: confidence_below_threshold.",
        },
    }
    calls, fake = _fake_select_resource(responses)
    monkeypatch.setattr(AIResourceManager, "select_resource", fake)
    _enriched, blockers = coordinator._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta={},
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "aido_lead"}],
        product_owner_selected_resource={"providerId": "llama_cpp", "model": "gemma-4-26b-a4b"},
    )
    assert len(calls) == 1
    assert calls[0].allowed_provider_ids == ["llama_cpp"]
    assert len(blockers) == 1
    assert blockers[0]["role"] == "aido_lead"


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


def test_architect_review_keeps_the_local_runtime_cause(coordinator, tmp_path, monkeypatch):
    reviews: list[dict] = []

    def fake_run(self, payload):
        return {
            "status": "failed",
            "verdict": "",
            "reason": "OpenAI-compatible execution failed: model_loading",
            "localRuntimeCause": "model_loading",
            "evidencePackage": {"id": "ev"},
        }

    monkeypatch.setattr(coordinator_module.ArchitectAgentRunner, "run", fake_run)
    monkeypatch.setattr(
        coordinator.repository,
        "update_loop_context",
        lambda loop_id, *, context: (
            reviews.append(context["durableRun"]["teamReviews"]) or {"id": loop_id, "context": context}
        ),
    )
    monkeypatch.setattr(coordinator, "_record_loop_event", lambda **kwargs: None)

    coordinator._run_team_review_phase(_review_run(tmp_path, {}))

    assert reviews[-1]["architect"]["status"] == "failed"
    assert reviews[-1]["architect"]["localRuntimeCause"] == "model_loading"


GLOBAL = {
    "globalRuntimeTeam": {
        "roleRuntimeOrder": {
            "product_owner": ["codex_cli", "nvidia_nim"],
            "developer": ["nvidia_nim", "codex_cli"],
            "architect": [],
            "security": ["nvidia_nim"],
            "technical_lead": ["codex_cli", "nvidia_nim"],
            "researcher": ["codex_cli", "nvidia_nim"],
        },
        "roleRuntimes": {
            "product_owner": "codex_cli",
            "developer": "nvidia_nim",
            "architect": None,
            "security": "nvidia_nim",
            "technical_lead": "codex_cli",
            "researcher": "codex_cli",
        },
        "source": {
            "product_owner": "general",
            "developer": "project",
            "architect": "automatic",
            "security": "automatic",
            "technical_lead": "inherited",
            "researcher": "inherited",
        },
        "allowedRuntimes": ["codex_cli", "nvidia_nim"],
    }
}


def test_the_global_team_order_is_the_allowlist_and_the_routing_preference(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    request = _team_request(coordinator, build, GLOBAL, product_owner_provider_id="codex_cli")
    assert request.allowed_provider_ids == ["nvidia_nim", "codex_cli"]
    assert request.preferred_provider_ids[:2] == ["nvidia_nim", "codex_cli"]
    assert request.preferred_resources[:2] == [
        {"provider": "nvidia_nim", "model": ""},
        {"provider": "codex_cli", "model": ""},
    ]
    # Con equipo por hilo nada cambia: allowlist de un proveedor y las preferencias de la política.
    thread_request = _team_request(coordinator, build, TEAM)
    assert thread_request.allowed_provider_ids == ["codex_cli"]
    policy = coordinator._resource_role_policy("backend_engineer")
    assert thread_request.preferred_provider_ids == policy["preferredProviderIds"]
    assert thread_request.preferred_resources == policy["preferredResources"]


@pytest.mark.parametrize("developer_source", ["project", "automatic"])
def test_with_a_global_team_a_role_without_candidates_is_never_widened_to_the_whole_catalog(
    coordinator, monkeypatch, developer_source
):
    """El orden automático ya contiene a todos los elegibles activos y el explícito es del operador:
    ampliar a todo el catálogo solo sumaría proveedores inactivos o no elegibles."""
    meta = {
        "globalRuntimeTeam": {
            **GLOBAL["globalRuntimeTeam"],
            "source": {**GLOBAL["globalRuntimeTeam"]["source"], "developer": developer_source},
        }
    }
    calls, select = _fake_select_resource(
        {
            ("nvidia_nim", "codex_cli"): {"selected": None, "candidates": []},
            None: {
                "selected": {"providerId": "gemini", "model": "g"},
                "candidates": [{"providerId": "gemini"}],
            },
        }
    )
    monkeypatch.setattr(AIResourceManager, "select_resource", select)
    schedule = {
        **SCHEDULE,
        "roles": [{"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}],
    }
    coordinator._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=meta,
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
        product_owner_selected_resource={"providerId": "codex_cli", "model": "gpt-5.5"},
    )
    assert [tuple(call.allowed_provider_ids or []) for call in calls] == [("nvidia_nim", "codex_cli")]


def test_without_a_global_snapshot_the_legacy_widening_still_applies(coordinator, monkeypatch):
    calls, select = _fake_select_resource(
        {
            ("codex_cli",): {"selected": None, "candidates": []},
            None: {
                "selected": {"providerId": "gemini", "model": "g"},
                "candidates": [{"providerId": "gemini"}],
            },
        }
    )
    monkeypatch.setattr(AIResourceManager, "select_resource", select)
    schedule = {
        **SCHEDULE,
        "roles": [{"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}],
    }
    coordinator._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta={},
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
        product_owner_selected_resource={"providerId": "codex_cli", "model": "gpt-5.5"},
    )
    assert [tuple(call.allowed_provider_ids or []) for call in calls] == [("codex_cli",), ()]


def test_the_global_order_outranks_the_role_policy_pins_in_the_real_ranking():
    """Los pins exactos de la política (tier 0) ganaban al comodín del orden (tier 1): el orden del
    operador no mandaba. Con ``provider_order`` los pins solo eligen el modelo dentro del proveedor."""
    codex = {"providerId": "codex_cli", "model": "gpt-5.5", "score": 0.9, "runtime": "codex_cli"}
    claude = {"providerId": "claude_code_cli", "model": "sonnet", "score": 0.5, "runtime": "claude_code_cli"}
    claude_other = {
        "providerId": "claude_code_cli",
        "model": "opus",
        "score": 0.95,
        "runtime": "claude_code_cli",
    }
    order = ["claude_code_cli", "codex_cli"]
    preferred = [
        {"provider": "claude_code_cli", "model": ""},
        {"provider": "codex_cli", "model": ""},
        {"provider": "codex_cli", "model": "gpt-5.5"},
        {"provider": "claude_code_cli", "model": "sonnet"},
    ]

    def pick(provider_order):
        return min(
            [codex, claude, claude_other],
            key=lambda item: AIResourceManager._selection_sort_key(item, order, preferred, provider_order),
        )

    assert pick([]) is codex  # comportamiento previo: el pin exacto de codex ganaba
    # Con orden estricto gana el proveedor primero y el pin elige su modelo (sonnet, no opus).
    assert pick(order) is claude


def test_every_global_team_request_carries_the_strict_provider_order(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    assert _team_request(coordinator, build, GLOBAL).provider_order == ["nvidia_nim", "codex_cli"]
    assert _team_request(coordinator, build, TEAM).provider_order == []
    assert _team_request(coordinator, build, {}).provider_order == []


def _run_schedule(coordinator, monkeypatch, meta, responses, *, jev_selects: bool):
    _calls, fake = _fake_select_resource(responses)
    recorded: list = []

    def select(self, request, *, record=True, **kwargs):
        if record:
            recorded.append(tuple(request.allowed_provider_ids or []))
        return fake(self, request, record=record, **kwargs)

    monkeypatch.setattr(AIResourceManager, "select_resource", select)
    monkeypatch.setattr(
        coordinator_module,
        "resolve_config",
        lambda connection, project_id: SimpleNamespace(selects_runtime=jev_selects),
    )
    coordinator._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=meta,
        team_schedule={
            **SCHEDULE,
            "roles": [{"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}],
        },
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
        product_owner_selected_resource={"providerId": "codex_cli", "model": "gpt-5.5"},
    )
    return recorded


def _with_developer_source(source: str) -> dict:
    team = GLOBAL["globalRuntimeTeam"]
    return {"globalRuntimeTeam": {**team, "source": {**team["source"], "developer": source}}}


def test_with_jev_selecting_an_automatic_order_is_tried_one_provider_at_a_time(coordinator, monkeypatch):
    """Jev ignora las preferencias; con varios proveedores que nadie eligió reabriría la ambigüedad."""
    calls = _run_schedule(
        coordinator,
        monkeypatch,
        _with_developer_source("automatic"),
        {
            ("nvidia_nim",): {"selected": None, "candidates": []},
            ("codex_cli",): {"selected": {"providerId": "codex_cli", "model": "m"}, "candidates": [{}]},
        },
        jev_selects=True,
    )
    # Los sondeos no registran: una sola decisión de ruteo, la del proveedor elegido.
    assert calls == [("codex_cli",)]


def test_with_jev_selecting_a_walk_without_candidates_records_one_decision_over_the_order(
    coordinator, monkeypatch
):
    calls = _run_schedule(
        coordinator,
        monkeypatch,
        _with_developer_source("automatic"),
        {
            ("nvidia_nim",): {"selected": None, "candidates": []},
            ("codex_cli",): {"selected": None, "candidates": []},
            ("nvidia_nim", "codex_cli"): {"selected": None, "candidates": []},
        },
        jev_selects=True,
    )
    assert calls == [("nvidia_nim", "codex_cli")]


def test_with_jev_selecting_an_explicit_order_keeps_the_whole_allowlist(coordinator, monkeypatch):
    calls = _run_schedule(
        coordinator,
        monkeypatch,
        _with_developer_source("project"),
        {
            ("nvidia_nim", "codex_cli"): {
                "selected": {"providerId": "nvidia_nim", "model": "m"},
                "candidates": [{}],
            }
        },
        jev_selects=True,
    )
    assert calls == [("nvidia_nim", "codex_cli")]


def test_in_shadow_mode_the_automatic_order_is_one_request_ranked_by_order(coordinator, monkeypatch):
    calls = _run_schedule(
        coordinator,
        monkeypatch,
        _with_developer_source("automatic"),
        {
            ("nvidia_nim", "codex_cli"): {
                "selected": {"providerId": "nvidia_nim", "model": "m"},
                "candidates": [{}],
            }
        },
        jev_selects=False,
    )
    assert calls == [("nvidia_nim", "codex_cli")]
