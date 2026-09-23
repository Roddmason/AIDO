"""Un runtime asignado que perdió su validación bloquea el loop con "Re-probar runtime".

Nunca cae en silencio a otro runtime: la remediación encola una validación por runtime vencido y,
cuando todos responden, reanuda el retry pendiente del mismo loop.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.executions import router as execution_router
from local_control_center.executions.repository import ExecutionRepository
from local_control_center.executions.router import OperationSpec
from local_control_center.executions.workloads import operation_workload
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.product_loop.phases.environment import select_product_owner_resources
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

TEAM = {
    "runtimeTeam": {
        "allowedRuntimes": ["codex_cli", "ollama", "nvidia_nim"],
        "roleRuntimes": {
            "product_owner": "codex_cli",
            "developer": "codex_cli",
            "architect": "ollama",
            "security": "ollama",
        },
    }
}
STALE_DETAILS = {
    "runtimeIds": ["codex_cli"],
    "staleRuntimes": [
        {"providerId": "codex_cli", "status": "never", "reason": "runtime_validation_required"}
    ],
    "missingRoles": [],
}


def _run(request_meta: dict) -> SimpleNamespace:
    return SimpleNamespace(
        project_id="project-gate",
        actor="aido_lead",
        loop={"id": "loop-1", "context": {}},
        thread_id=None,
        request_meta=request_meta,
        preferred_runtime=None,
        product_owner=SimpleNamespace(),
    )


@pytest.fixture
def gate(tmp_path: Path, monkeypatch):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked: dict = {}

        def fake_block(loop, **kwargs):
            blocked.update(kwargs)
            return {"status": "blocked"}

        monkeypatch.setattr(coordinator, "_transition_run_state", lambda loop, **kwargs: loop)
        monkeypatch.setattr(coordinator, "_durable_run_patch", lambda loop, patch: patch)
        monkeypatch.setattr(coordinator, "_block_run", fake_block)
        monkeypatch.setattr(
            coordinator,
            "_product_owner_resource_selection",
            lambda **kwargs: (
                {"selected": None},
                {"role": "product_owner", "reason": "gate passed", "taskId": "t"},
            ),
        )
        yield connection, coordinator, blocked


def test_runtime_check_blocks_when_an_assigned_runtime_lost_its_validation(gate):
    _connection, coordinator, blocked = gate
    assert select_product_owner_resources(coordinator, _run(TEAM)) == {"status": "blocked"}
    assert blocked["stage"] == "runtime_team"
    assert blocked["details"]["runtimeIds"] == ["codex_cli", "ollama"]


def test_runtime_check_blocks_a_team_with_missing_roles(gate):
    connection, coordinator, blocked = gate
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    team = {"runtimeTeam": {"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"developer": "codex_cli"}}}
    select_product_owner_resources(coordinator, _run(team))
    assert blocked["stage"] == "runtime_team"
    assert blocked["details"]["missingRoles"] == ["product_owner"]


def test_optional_roles_left_unassigned_do_not_block_the_run(gate):
    connection, coordinator, blocked = gate
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    team = {
        "runtimeTeam": {
            "allowedRuntimes": ["codex_cli"],
            "roleRuntimes": {"developer": "codex_cli", "product_owner": "codex_cli"},
        }
    }
    select_product_owner_resources(coordinator, _run(team))
    assert blocked["stage"] == "resource_manager"


def test_a_selected_runtime_without_a_role_does_not_block_the_run(gate):
    connection, coordinator, blocked = gate
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt")
    select_product_owner_resources(coordinator, _run(TEAM))
    assert blocked["stage"] == "resource_manager"


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Runtime team gate", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Gate"
        )
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Gate",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"]},
                        "blockedStage": "runtime_team",
                        "blockedReason": "Runtimes without a fresh validation: codex_cli.",
                    }
                },
            }
        )
        threads.set_status(thread["id"], "blocked")
        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="runtime_team",
            reason="Runtimes without a fresh validation: codex_cli (runtime_validation_required).",
            details=STALE_DETAILS,
        )
        yield service, actions, project["id"], connection


def _platform() -> SimpleNamespace:
    spec = OperationSpec("models.validate_runtime", "remote_llm_light")
    return SimpleNamespace(execution_handlers={"models.validate_runtime": (spec, lambda **kwargs: None)})


def test_the_blocker_offers_retest_first_and_retry_second(lane):
    _service, actions, _project_id, _connection = lane
    assert {action["blockerType"] for action in actions} == {"runtime_team_validation_expired"}
    assert [action["actionType"] for action in actions] == ["revalidate_runtime", "retry_loop"]
    assert actions[0]["primary"] is True
    assert actions[0]["payload"]["runtimeIds"] == ["codex_cli"]


def test_the_retest_remediation_only_enqueues_so_it_reserves_light_work(lane):
    _service, actions, _project_id, connection = lane
    spec = OperationSpec("remediations.execute", "agent_cli")
    assert operation_workload(connection, spec, {"remediation_id": actions[0]["id"]}) == "qa_light"


def test_retest_queues_one_validation_per_stale_runtime_and_keeps_the_run_blocked(lane, monkeypatch):
    service, actions, project_id, _connection = lane
    enqueued: list[tuple] = []

    def fake_enqueue(platform, spec, arguments, *, result_status_code=200, project_id=None):
        enqueued.append((spec.name, arguments, project_id))
        return {
            "executionId": f"exec-{len(enqueued)}",
            "jobId": "job",
            "operation": spec.name,
            "status": "queued",
        }

    monkeypatch.setattr(execution_router, "enqueue_registered_operation", fake_enqueue)
    monkeypatch.setattr(
        BlockerRemediationService, "_retry_loop", lambda self, **kwargs: pytest.fail("must not retry yet")
    )
    result = service.execute(actions[0]["id"], platform=_platform())
    assert result["execution"]["status"] == "validating"
    assert result["execution"]["executionIds"] == ["exec-1"]
    assert enqueued == [
        (
            "models.validate_runtime",
            {"provider_id": "codex_cli", "body": {"projectId": project_id}},
            project_id,
        )
    ]
    assert result["remediation"]["status"] == "pending"


def test_retest_again_while_a_probe_is_in_flight_does_not_enqueue_another(lane, monkeypatch):
    service, actions, _project_id, connection = lane
    enqueued: list[str] = []

    def real_enqueue(platform, spec, arguments, *, result_status_code=200, project_id=None):
        enqueued.append(arguments["provider_id"])
        return ExecutionRepository(connection).enqueue(
            operation=spec.name,
            workload_class="remote_llm_light",
            arguments={"sealedInput": "test"},
            project_id=project_id,
            cwd=".",
            result_status_code=result_status_code,
        )

    monkeypatch.setattr(execution_router, "enqueue_registered_operation", real_enqueue)
    first = service.execute(actions[0]["id"], platform=_platform())
    second = service.execute(actions[0]["id"], platform=_platform())
    assert enqueued == ["codex_cli"]
    assert second["execution"]["status"] == "validating"
    assert second["execution"]["executionIds"] == first["execution"]["executionIds"]
    assert second["remediation"]["status"] == "pending"

    ExecutionRepository(connection).request_cancel(first["execution"]["executionIds"][0], reason="test")
    third = service.execute(actions[0]["id"], platform=_platform())
    assert enqueued == ["codex_cli", "codex_cli"]
    assert third["execution"]["executionIds"] != first["execution"]["executionIds"]


def test_a_validated_runtime_resumes_the_pending_retry(lane, monkeypatch):
    service, actions, _project_id, connection = lane
    retried: list[str] = []

    def fake_retry(self, *, action, payload=None):
        retried.append(action["id"])
        return {"status": "queued", "action": "retry_loop", "jobId": "job-retry"}

    monkeypatch.setattr(BlockerRemediationService, "_retry_loop", fake_retry)
    service.repository.merge_payload(actions[0]["id"], {"validationExecutions": {"codex_cli": "exec-1"}})
    assert service.resume_after_runtime_validation("codex_cli") == []
    assert retried == []
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    assert service.resume_after_runtime_validation("ollama") == []
    resumed = service.resume_after_runtime_validation("codex_cli")
    assert [item["execution"]["status"] for item in resumed] == ["queued"]
    assert retried == [actions[1]["id"]]
    assert service.repository.get(actions[1]["id"])["status"] == "resolved"
    assert service.repository.get(actions[0]["id"])["status"] == "resolved"


def test_retest_without_the_registered_operation_is_blocked_not_silent(lane, monkeypatch):
    service, actions, _project_id, _connection = lane
    result = service.execute(actions[0]["id"], platform=SimpleNamespace(execution_handlers={}))
    assert result["execution"]["status"] == "blocked"
    assert "models.validate_runtime" in result["execution"]["reason"]
    assert result["remediation"]["status"] == "pending"


def test_a_probe_the_operator_did_not_request_from_retest_does_not_resume_the_loop(lane, monkeypatch):
    service, actions, _project_id, connection = lane
    monkeypatch.setattr(
        BlockerRemediationService, "_retry_loop", lambda self, **kwargs: pytest.fail("must not retry")
    )
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    assert service.resume_after_runtime_validation("codex_cli") == []
    assert service.repository.get(actions[0]["id"])["status"] == "pending"
    assert service.repository.get(actions[1]["id"])["status"] == "pending"


def test_retest_ignores_runtime_ids_sent_by_the_client(lane, monkeypatch):
    service, actions, _project_id, _connection = lane
    enqueued: list[str] = []

    def fake_enqueue(platform, spec, arguments, *, result_status_code=200, project_id=None):
        enqueued.append(arguments["provider_id"])
        return {"executionId": "exec-1", "jobId": "job", "operation": spec.name, "status": "queued"}

    monkeypatch.setattr(execution_router, "enqueue_registered_operation", fake_enqueue)
    service.execute(actions[0]["id"], payload={"runtimeIds": ["ollama"]}, platform=_platform())
    assert enqueued == ["codex_cli"]
