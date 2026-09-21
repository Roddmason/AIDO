"""Role/cost policy failures must not be diagnosed as an absent executable runtime."""

from contextlib import closing

import pytest

from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _details():
    return {
        "resourceBlockers": [
            {
                "role": "aido_lead",
                "decision": {
                    "selected": None,
                    "approvalRequired": False,
                    "decisionReason": (
                        "Jev runtime selection blocked: no_eligible_candidates. "
                        "Deferred: unknown cost requires approval (6)."
                    ),
                    "rejected": [
                        {"providerId": "codex_cli", "model": "gpt-5.5", "reason": "role_blocks_candidate"},
                        {"providerId": "other-cli", "reason": "runtime_not_executable:unavailable"},
                    ],
                    "policyResult": {
                        "decisionEngine": {
                            "mode": "runtime_selection",
                            "reasonCode": "no_eligible_candidates",
                        },
                        "runtimePreflight": {
                            "attempts": 0,
                            "validated": [],
                            "deferredCount": 6,
                            "deferredReasonCounts": {"preflight_unknown_cost_requires_approval": 6},
                            "deferred": [
                                {
                                    "providerId": "remote-account",
                                    "model": "catalogued-model",
                                    "reason": "preflight_unknown_cost_requires_approval",
                                }
                            ],
                        },
                    },
                },
            }
        ]
    }


@pytest.fixture
def lane(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Policy remediation", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Role policy"
        )
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Blocked role",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"]},
                        "blockedStage": "resource_manager",
                        "blockedReason": "No eligible candidate for aido_lead.",
                    }
                },
            }
        )
        threads.set_status(thread["id"], "blocked")
        yield connection, project, thread, loop, BlockerRemediationService(connection, root=tmp_path)


def _old_action(
    lane,
    *,
    action_type="validate_runtime",
    blocker_type="runtime_not_executable",
    project_id=None,
    thread_id=None,
    details=None,
):
    _, project, thread, loop, service = lane
    return service.repository.create_action(
        project_id=project_id or project["id"],
        thread_id=thread_id or thread["id"],
        loop_id=loop["id"],
        stage="resource_manager",
        blocker_type=blocker_type,
        action_type=action_type,
        title="No executable runtime",
        description="Legacy incorrect diagnosis.",
        payload={"details": details if details is not None else _details(), "section": "providers-cli"},
    )


def test_role_and_unknown_cost_blocker_opens_routing_without_offering_unselected_approval(lane):
    _, project, thread, loop, service = lane
    actions = service.create_for_blocked_run(
        project_id=project["id"],
        thread_id=thread["id"],
        loop_id=loop["id"],
        stage="resource_manager",
        reason="No eligible candidate for aido_lead.",
        details=_details(),
    )
    assert {action["blockerType"] for action in actions} == {"resource_manager_unconfigured"}
    assert [action["actionType"] for action in actions] == ["open_settings_section", "retry_loop"]
    assert actions[0]["payload"]["section"] == "routing"
    assert actions[0]["payload"]["blockedRoles"] == ["aido_lead"]
    summary = actions[0]["payload"]["resourceBlockers"][0]
    assert summary["selected"] is None
    assert summary["rejected"][0]["reason"] == "role_blocks_candidate"
    assert "unknown cost requires approval" in summary["decisionReason"]
    assert "executable" not in actions[0]["description"].lower()


@pytest.mark.parametrize("operation", ["list", "execute"])
@pytest.mark.parametrize("legacy_type", ["runtime_not_executable", "resource_manager_unconfigured"])
def test_legacy_policy_card_is_dismissed_and_replaced_preserving_manual_actions(lane, operation, legacy_type):
    _, _, thread, _, service = lane
    old = _old_action(lane, action_type="open_settings_section", blocker_type=legacy_type)
    manual = [
        _old_action(lane, action_type=kind, blocker_type=legacy_type)
        for kind in ("answer_question", "approve_resource_decision", "continue_plan_only")
    ]
    if operation == "execute":
        result = service.execute(old["id"], platform=object())
        assert result["execution"]["status"] == "blocked"
    else:
        service.list_for_thread(thread_id=thread["id"])
    assert service.repository.get(old["id"])["status"] == "dismissed"
    assert service.repository.get(old["id"])["payload"] == old["payload"]
    assert all(service.repository.get(item["id"])["status"] == "pending" for item in manual)
    current = [
        item
        for item in service.repository.list_for_thread(thread["id"])
        if item["status"] == "pending" and item["id"] not in {action["id"] for action in manual}
    ]
    assert {item["blockerType"] for item in current} == {"resource_manager_unconfigured"}
    assert (
        next(item for item in current if item["actionType"] == "open_settings_section")["payload"]["section"]
        == "routing"
    )
    before = service.repository.list_for_thread(thread["id"])
    assert service.list_for_thread(thread_id=thread["id"]) == before


@pytest.mark.parametrize(
    "constraint",
    [
        "runtime_policy_denied",
        "heavy_workload_capacity",
        "aggregate_memory_budget",
        "host_cpu_saturated",
        "light_workload_capacity",
    ],
)
def test_preflight_constraints_replace_false_missing_runtime_without_probing(lane, constraint):
    _, _, thread, _, service = lane
    details = _details()
    decision = details["resourceBlockers"][0]["decision"]
    decision["rejected"] = [{"providerId": "ollama", "reason": "runtime_not_executable:resource_wait"}]
    preflight = decision["policyResult"]["runtimePreflight"]
    preflight["deferredReasonCounts"] = {} if constraint == "runtime_policy_denied" else {constraint: 7}
    preflight["rejected"] = (
        [{"providerId": "codex_cli", "reason": constraint}] if constraint == "runtime_policy_denied" else []
    )
    decision["decisionReason"] = f"Automatic validation: 0 attempted, 0 validated. Deferred: {constraint}."
    old = _old_action(lane, details=details)
    current = [
        a for a in service.list_for_thread(thread_id=thread["id"], summary=True) if a["status"] == "pending"
    ]
    assert service.repository.get(old["id"])["status"] == "dismissed"
    assert {a["blockerType"] for a in current} == {"resource_manager_unconfigured"}
    assert {a["actionType"] for a in current} == {"open_settings_section", "retry_loop"}
    assert (
        next(a for a in current if a["actionType"] == "open_settings_section")["payload"]["section"]
        == "routing"
    )
    assert constraint in current[0]["technicalReason"]
    assert [
        a for a in service.list_for_thread(thread_id=thread["id"], summary=True) if a["status"] == "pending"
    ] == current


@pytest.mark.parametrize("count", [0, -1, True, "7", None])
def test_capacity_classification_requires_positive_structured_count(lane, count):
    details = _details()
    decision = details["resourceBlockers"][0]["decision"]
    decision["rejected"] = [{"providerId": "ollama", "reason": "runtime_not_executable:unavailable"}]
    decision["policyResult"]["runtimePreflight"] = {
        "deferredReasonCounts": {"heavy_workload_capacity": count}
    }
    assert (
        lane[-1]._blocker_type(stage="resource_manager", reason="Unavailable", details=details)
        == "runtime_not_executable"
    )


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("runtime_absent", "runtime_not_executable"),
        ("privacy", "resource_manager_privacy_blocked"),
        ("jev_timeout", "decision_engine_unavailable"),
        ("selected_approval", "resource_manager_approval_required"),
        ("role_only", "resource_manager_unconfigured"),
        ("cost_only", "resource_manager_unconfigured"),
    ],
)
def test_structured_policy_diagnosis_does_not_replace_other_causes(lane, scenario, expected):
    details = _details()
    decision = details["resourceBlockers"][0]["decision"]
    reason = "No eligible candidate."
    if scenario in {"runtime_absent", "cost_only"}:
        decision["rejected"] = decision["rejected"][1:]
    if scenario in {"runtime_absent", "role_only"}:
        decision["policyResult"].pop("runtimePreflight")
    if scenario == "privacy":
        decision["rejected"] = [{"providerId": "remote-account", "reason": "privacy_blocks_remote"}]
        decision["policyResult"].pop("runtimePreflight")
        decision["policyResult"]["decisionEngine"]["reasonCode"] = "privacy_blocked"
    if scenario == "jev_timeout":
        decision["policyResult"]["decisionEngine"]["reasonCode"] = "timeout"
    if scenario == "selected_approval":
        decision["selected"] = {"providerId": "codex_cli", "model": "gpt-5.5"}
        decision["approvalRequired"] = True
        reason = "Selected resource requires approval."
    assert lane[-1]._blocker_type(stage="resource_manager", reason=reason, details=details) == expected


@pytest.mark.parametrize("mismatch", ["project", "thread"])
def test_reclassification_does_not_replace_foreign_loop_actions(lane, tmp_path, mismatch):
    connection, project, _thread, _, service = lane
    foreign_project = ProjectsRepository(connection).create_project(
        name="Other project", path=tmp_path / "other", template_id="other"
    )
    foreign_thread = ThreadsRepository(connection).create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Other thread"
    )
    old = _old_action(
        lane,
        project_id=foreign_project["id"] if mismatch == "project" else None,
        thread_id=foreign_thread["id"] if mismatch == "thread" else None,
    )
    service.list_for_thread(thread_id=old["threadId"])
    assert service.repository.get(old["id"])["status"] == "pending"
    assert service.repository.get(old["id"])["payload"] == old["payload"]
