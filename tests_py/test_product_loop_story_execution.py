"""Ejecución por historia del product loop: un run del developer por historia, con QA y rework propios.

Fija los criterios del diseño 2026-09-22 (§1 y §3): N historias → N runs acotados a su historia,
estados ``todo → in_progress → qa → done`` persistidos con eventos ``story_progress`` y cursor
durable, presupuesto de rework por historia, historia sin cambios cerrada como ``noop`` y bloqueo
con cursor al agotar el rework.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop import coordinator as coordinator_module
from local_control_center.product_loop.coordinator import DEFAULT_AUTO_REWORK_ROUNDS, ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from tests_py.test_product_loop_coordinator import (
    _AssessmentRunner,
    _ControlledRuntime,
    _GitGate,
    _product_owner_result,
    _ProductOwnerRunner,
    _RoleTaskPlanner,
    _SecurityGate,
    _seed_ai_resource,
    _TechnicalLeadPlanner,
    _workspace_project,
)
from tests_py.test_product_loop_coordinator import _controlled_ollama_daemon as _controlled_ollama_daemon

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

SECOND_STORY = {
    "epicTitle": "Onboarding readiness",
    "title": "Setup reminders",
    "asA": "operations lead",
    "iWant": "to be reminded of pending setup steps",
    "soThat": "I do not forget onboarding work",
    "businessValue": "medium",
    "acceptanceCriteria": ["Given a pending step, when a day passes, then a reminder is shown."],
}


def _two_story_po() -> _ProductOwnerRunner:
    result = _product_owner_result("backlog_ready")
    stories = [*result["userStories"], dict(SECOND_STORY)]
    result["userStories"] = stories
    result["output"]["userStories"] = stories
    return _ProductOwnerRunner(result)


class _PerStoryRuntime(_ControlledRuntime):
    """Runtime controlado cuyo resultado depende de la posición de la historia del payload."""

    def __init__(
        self,
        *,
        qa_failures: dict[int, int] | None = None,
        changed_files: dict[int, list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.qa_failures = dict(qa_failures or {})
        self.changed_by_story = dict(changed_files or {})
        self.story_order: list[str] = []

    def story_position(self, payload: dict[str, Any]) -> int:
        story_id = str(payload["agentTasks"][0]["storyId"])
        if story_id not in self.story_order:
            self.story_order.append(story_id)
        return self.story_order.index(story_id) + 1

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        position = self.story_position(payload)
        remaining = self.qa_failures.get(position, 0)
        self.status_value = "qa_failed" if remaining else "completed"
        if remaining:
            self.qa_failures[position] = remaining - 1
        self.changed_files = self.changed_by_story.get(position, [f"src/story_{position}.py"])
        result = super().run(payload)
        result["evidencePackage"] = {**result["evidencePackage"], "id": f"evidence-story-{position}"}
        result["qaResults"] = [{**item, "command": f"qa story {position}"} for item in self.qa_results]
        return result


def _run(
    coordinator: ProductLoopCoordinator,
    project: dict[str, Any],
    runtime: _ControlledRuntime,
    *,
    security: _SecurityGate | None = None,
    technical_lead: Any | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return coordinator.run_user_message(
        project_id=project["id"],
        message="Implement the onboarding readiness experience story by story.",
        preferred_runtime="controlled_test_runtime",
        runtime_runner=runtime,
        git_service=_GitGate(),
        product_owner_runner=_two_story_po(),
        assessment_runner=_AssessmentRunner(),
        technical_lead_runner=technical_lead or _TechnicalLeadPlanner(),
        security_runner=security or _SecurityGate(),
        **kwargs,
    )


def test_each_story_gets_its_own_scoped_developer_run(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-scoped")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        assert len(runtime.run_payloads) == 2
        first, second = runtime.run_payloads
        assert {task["storyId"] for task in first["agentTasks"]} == {runtime.story_order[0]}
        assert {task["storyId"] for task in second["agentTasks"]} == {runtime.story_order[1]}
        assert "Readiness checklist" in first["storySpecs"]
        assert "Setup reminders" not in first["storySpecs"]
        assert "Setup reminders" in second["storySpecs"]
        assert "Readiness checklist" not in second["storySpecs"]
        assert first["taskId"].endswith(":s1")
        assert second["taskId"].endswith(":s2")
        assert first["preferredRuntime"] == second["preferredRuntime"]
        assert first["resourceSelection"] == second["resourceSelection"]
        triggers = [item.get("trigger") for item in result["transitions"]]
        assert triggers.count("next_story") == 1
        review = result["loop"]["context"]["durableRun"]["review"]
        assert review["changedFiles"] == ["src/story_1.py", "src/story_2.py"]


def test_story_statuses_events_and_cursor_follow_each_story(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-statuses")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        backlog = BacklogRepository(connection)
        assert {story["status"] for story in backlog.list_user_stories(project_id=project["id"])} == {"done"}
        assert {task["status"] for task in backlog.list_agent_tasks(project_id=project["id"])} == {"done"}
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        progress_events = [
            (
                event["payload"]["storyId"],
                event["payload"]["status"],
                event["payload"]["index"],
                event["payload"]["total"],
            )
            for event in ThreadsRepository(connection).list_events(thread_id)
            if event["type"] == "story_progress"
        ]
        first, second = runtime.story_order
        assert progress_events == [
            (first, "in_progress", 1, 2),
            (first, "qa", 1, 2),
            (first, "done", 1, 2),
            (second, "in_progress", 2, 2),
            (second, "qa", 2, 2),
            (second, "done", 2, 2),
        ]
        cursor = result["loop"]["context"]["durableRun"]["storyProgress"]
        assert [
            (entry["storyId"], entry["status"], entry["runs"], entry["qaVerdict"]) for entry in cursor
        ] == [
            (first, "done", 1, "passed"),
            (second, "done", 1, "passed"),
        ]
        assert all(len(entry["fingerprint"]) == 64 for entry in cursor)


def test_the_rework_budget_resets_for_every_story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _PerStoryRuntime(qa_failures={1: 1, 2: 1})
    original_start = ProductLoopCoordinator.start

    def start_with_policy(self: ProductLoopCoordinator, **kwargs: Any) -> dict[str, Any]:
        return original_start(self, **{**kwargs, "max_rework_rounds": 1})

    monkeypatch.setattr(ProductLoopCoordinator, "start", start_with_policy)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-rework-budget")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        suffixes = [payload["taskId"].split(":", 1)[1] for payload in runtime.run_payloads]
        assert suffixes == ["s1", "s1:r1", "s2", "s2:r1"]


def test_a_story_that_exhausts_rework_blocks_the_loop_and_keeps_the_cursor(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime(qa_failures={2: 99})
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-exhausted")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "blocked"
        durable = result["loop"]["context"]["durableRun"]
        assert durable["blockedStage"] == "qa_rework"
        assert len(runtime.run_payloads) == 1 + 1 + DEFAULT_AUTO_REWORK_ROUNDS
        first, second = runtime.story_order
        assert [(entry["storyId"], entry["status"]) for entry in durable["storyProgress"]] == [
            (first, "done"),
            (second, "blocked"),
        ]
        assert durable["storyProgress"][1]["reason"]
        backlog = BacklogRepository(connection)
        assert backlog.get_user_story(first)["status"] == "done"
        blocked_story = backlog.get_user_story(second)
        assert blocked_story["status"] == "blocked"
        assert blocked_story["metadata"]["blockedReason"]


def test_a_story_without_changes_is_closed_as_noop(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime(changed_files={1: []})
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-noop")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        first, _second = runtime.story_order
        story = BacklogRepository(connection).get_user_story(first)
        assert story["status"] == "done"
        assert story["metadata"]["outcome"] == "noop"
        assert "story_noop" in [item.get("trigger") for item in result["transitions"]]
        assert result["loop"]["context"]["durableRun"]["review"]["changedFiles"] == ["src/story_2.py"]


def test_approval_carries_the_qa_and_evidence_of_every_story(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-evidence")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        durable = result["loop"]["context"]["durableRun"]
        job = JobsRepository(connection).get_job(durable["approval"]["jobId"])
        action = JobsRepository(connection).get_action_request(durable["approval"]["actionRequestId"])
        for payload in (job["payload"], action["payload"]):
            assert "evidence-story-1" in payload["evidenceRefs"]
            assert "evidence-story-2" in payload["evidenceRefs"]
        security_evidence = EvidenceRepository(connection).get_evidence_package(
            job["payload"]["evidenceRefs"][-1]
        )
        commands = [item.get("command") for item in security_evidence["testResults"]]
        assert "qa story 1" in commands
        assert "qa story 2" in commands
        cursor = durable["storyProgress"]
        assert {entry["runtime"] for entry in cursor} == {"controlled_test_runtime"}


def test_a_story_without_llm_tasks_is_planned_by_the_deterministic_fallback(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-tl-fallback")

        result = _run(
            ProductLoopCoordinator(connection, root=tmp_path),
            project,
            runtime,
            technical_lead=_RoleTaskPlanner(["backend_engineer"]),
        )

        durable = result["loop"]["context"]["durableRun"]
        assert durable.get("blockedStage") != "technical_lead"
        assert len(runtime.run_payloads) == 2
        backlog = BacklogRepository(connection)
        reminders = next(
            story
            for story in backlog.list_user_stories(project_id=project["id"])
            if story["title"] == "Setup reminders"
        )
        fallback_story_ids = {
            task["storyId"]
            for task in backlog.list_agent_tasks(project_id=project["id"])
            if task["metadata"].get("technicalLeadFallback") is True
        }
        assert fallback_story_ids == {reminders["id"]}
        thread_id = durable["thread"]["projectThreadId"]
        fallback_events = [
            event["payload"]["storyId"]
            for event in ThreadsRepository(connection).list_events(thread_id)
            if event["type"] == "technical_lead_fallback"
        ]
        assert fallback_events == [reminders["id"]]


def test_a_story_uncovered_by_llm_and_fallback_blocks_before_any_developer_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _EmptyDeterministicPlanner:
        def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"agent_tasks": [], "task_dependencies": []}

    monkeypatch.setattr(coordinator_module, "TechnicalLeadPlanner", _EmptyDeterministicPlanner)
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-uncovered")

        result = _run(
            ProductLoopCoordinator(connection, root=tmp_path),
            project,
            runtime,
            technical_lead=_RoleTaskPlanner(["backend_engineer"]),
        )

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "technical_lead"
        assert "Setup reminders" in result["reason"]
        assert runtime.run_payloads == []
