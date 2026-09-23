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

from local_control_center.backlog.board import story_fingerprint
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop import coordinator as coordinator_module
from local_control_center.product_loop.coordinator import DEFAULT_AUTO_REWORK_ROUNDS, ProductLoopCoordinator
from local_control_center.product_loop.phases.story_loop import match_carried_over
from local_control_center.security_policy.git_command_runner import git_available
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workspaces_projects import git_worktrees
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.test_product_loop_coordinator import (
    _AssessmentRunner,
    _ControlledRuntime,
    _git_workspace_project,
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


class _WorkspaceWritingRuntime(_PerStoryRuntime):
    """Escribe un archivo real por historia en el worktree asignado, como haría un runtime de código."""

    def __init__(self, connection: Any, root: Path) -> None:
        super().__init__()
        self.connection = connection
        self.root = root

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        position = self.story_position(payload)
        workspace = WorkspacesRepository(self.connection, root=self.root).get_workspace(
            payload["workspaceId"]
        )
        relative = f"src/story_{position}.py"
        target = Path(workspace["path"]) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"STORY = {position}\n", encoding="utf-8")
        self.changed_by_story[position] = [relative]
        return super().run(payload)


def test_security_and_approval_review_the_cumulative_git_diff(tmp_path: Path) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the cumulative worktree diff")
    security = _SecurityGate()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "cumulative-git")
        runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime, security=security)

        assert result["status"] == "awaiting_approval"
        review = result["loop"]["context"]["durableRun"]["review"]
        assert review["state"] == "captured"
        assert review["changedFiles"] == ["src/story_1.py", "src/story_2.py"]
        artifact_id = security.run_payloads[0]["diffArtifactId"]
        assert artifact_id
        artifact = EvidenceRepository(connection).get_artifact_by_id(artifact_id)
        assert artifact["kind"] == "git_patch"
        assert artifact["metadata"]["name"] == "product-loop-cumulative.diff"
        patch = Path(artifact["path"]).read_text(encoding="utf-8")
        assert "src/story_1.py" in patch
        assert "src/story_2.py" in patch
        approval = result["loop"]["context"]["durableRun"]["approval"]
        assert approval["patchArtifactIds"] == [artifact_id]
        jobs = JobsRepository(connection)
        assert jobs.get_job(approval["jobId"])["payload"]["patchArtifactIds"] == [artifact_id]
        assert jobs.get_action_request(approval["actionRequestId"])["payload"]["patchArtifactIds"] == [
            artifact_id
        ]


def test_a_story_commit_failure_blocks_before_qa_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the per-story commit")
    security = _SecurityGate()

    def failing_commit(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "commit_failed", "stderr": "forced commit failure"}

    monkeypatch.setattr(git_worktrees, "commit_workspace_changes", failing_commit)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "commit-failure")
        runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime, security=security)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "review"
        assert "commit" in result["reason"].lower()
        assert "forced commit failure" in result["reason"]
        assert len(runtime.run_payloads) == 1
        assert security.run_payloads == []
        first = runtime.story_order[0]
        assert BacklogRepository(connection).get_user_story(first)["status"] == "blocked"


def test_uncommitted_work_blocks_the_cumulative_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the cumulative worktree diff")
    security = _SecurityGate()

    def commit_that_leaves_the_tree_dirty(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "committed", "commit": "0" * 40}

    monkeypatch.setattr(git_worktrees, "commit_workspace_changes", commit_that_leaves_the_tree_dirty)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "cumulative-dirty")
        runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime, security=security)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "review"
        assert "uncommitted" in result["reason"].lower()
        assert "src/" in result["reason"]
        assert security.run_payloads == []


def _fresh_host_sample(connection: Any) -> None:
    """Registra una muestra de host fresca antes del reintento.

    La readiness descarta muestras de más de 30 s (``resource_snapshot_stale``) y un reintento real corre
    con una muestra nueva, no con la del primer run, que en un host cargado ya venció.
    """
    ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())


def test_a_retry_skips_stories_already_done_in_the_source_loop(tmp_path: Path) -> None:
    first_runtime = _PerStoryRuntime(qa_failures={2: 99})
    retry_runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-retry")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked = _run(coordinator, project, first_runtime)
        assert blocked["status"] == "blocked"
        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]

        _fresh_host_sample(connection)
        retried = _run(
            coordinator,
            project,
            retry_runtime,
            thread_id=thread_id,
            run_metadata={
                "retryOfLoopId": blocked["loop"]["id"],
                "functionalityDecision": "continue_existing",
            },
        )

        assert retried["status"] == "awaiting_approval"
        assert len(retry_runtime.run_payloads) == 1
        payload = retry_runtime.run_payloads[0]
        assert "Setup reminders" in payload["storySpecs"]
        assert "Readiness checklist" not in payload["storySpecs"]
        assert payload["taskId"].endswith(":s2")
        cursor = retried["loop"]["context"]["durableRun"]["storyProgress"]
        assert [(entry["status"], entry["outcome"]) for entry in cursor] == [
            ("done", "carried_over"),
            ("done", None),
        ]


def test_a_retry_follows_the_retry_chain_to_the_last_loop_with_story_progress(tmp_path: Path) -> None:
    first_runtime = _PerStoryRuntime(qa_failures={2: 99})
    retry_runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-retry-chain")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked = _run(coordinator, project, first_runtime)
        assert blocked["status"] == "blocked"
        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        blocked_before_stories = coordinator.repository.create_loop(
            {
                "projectId": project["id"],
                "title": "Retry blocked before the story loop",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread_id},
                        "requestMeta": {"retryOfLoopId": blocked["loop"]["id"]},
                    }
                },
            }
        )

        _fresh_host_sample(connection)
        retried = _run(
            coordinator,
            project,
            retry_runtime,
            thread_id=thread_id,
            run_metadata={
                "retryOfLoopId": blocked_before_stories["id"],
                "functionalityDecision": "continue_existing",
            },
        )

        assert retried["status"] == "awaiting_approval"
        assert len(retry_runtime.run_payloads) == 1
        assert "Setup reminders" in retry_runtime.run_payloads[0]["storySpecs"]
        cursor = retried["loop"]["context"]["durableRun"]["storyProgress"]
        assert [(entry["status"], entry["outcome"]) for entry in cursor] == [
            ("done", "carried_over"),
            ("done", None),
        ]


def test_a_retry_marker_from_another_thread_never_skips_stories(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-foreign-retry")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        emitted = _two_story_po().result["userStories"]
        progress = [
            {
                "storyId": f"foreign-{index}",
                "index": index,
                "title": story["title"],
                "status": "done",
                "fingerprint": story_fingerprint(story, story["acceptanceCriteria"]),
                "outcome": None,
            }
            for index, story in enumerate(emitted, start=1)
        ]
        foreign = coordinator.repository.create_loop(
            {
                "projectId": project["id"],
                "title": "Foreign loop",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {"thread": {"projectThreadId": "thread-foreign"}, "storyProgress": progress}
                },
            }
        )

        result = _run(
            coordinator,
            project,
            runtime,
            run_metadata={"retryOfLoopId": foreign["id"], "functionalityDecision": "continue_existing"},
        )

        assert result["status"] == "awaiting_approval"
        assert len(runtime.run_payloads) == 2


def test_a_retry_with_every_story_done_runs_no_developer(tmp_path: Path) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the cumulative worktree diff")
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "per-story-retry-all-done")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked = _run(
            coordinator,
            project,
            _WorkspaceWritingRuntime(connection, tmp_path),
            security=_SecurityGate(verdict="blocked", reason="Critical security finding blocks completion."),
        )
        assert blocked["status"] == "blocked"
        assert {entry["status"] for entry in blocked["loop"]["context"]["durableRun"]["storyProgress"]} == {
            "done"
        }
        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        retry_runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        _fresh_host_sample(connection)
        retried = _run(
            coordinator,
            project,
            retry_runtime,
            thread_id=thread_id,
            run_metadata={
                "retryOfLoopId": blocked["loop"]["id"],
                "functionalityDecision": "continue_existing",
            },
        )

        assert retried["status"] == "awaiting_approval"
        assert retry_runtime.run_payloads == []
        assert "stories_already_done" in [item.get("trigger") for item in retried["transitions"]]
        cursor = retried["loop"]["context"]["durableRun"]["storyProgress"]
        assert [(entry["status"], entry["outcome"]) for entry in cursor] == [
            ("done", "carried_over"),
            ("done", "carried_over"),
        ]
        assert retried["loop"]["context"]["durableRun"]["review"]["changedFiles"] == [
            "src/story_1.py",
            "src/story_2.py",
        ]


def test_carried_over_matching_is_one_to_one_by_fingerprint() -> None:
    batches = [
        {"storyId": "current-1", "story": {"id": "current-1"}, "tasks": [{"id": "t1"}]},
        {"storyId": "current-2", "story": {"id": "current-2"}, "tasks": [{"id": "t2"}]},
        {"storyId": "current-3", "story": {"id": "current-3"}, "tasks": [{"id": "t3"}]},
    ]
    fingerprints = {"current-1": "same", "current-2": "same", "current-3": "other"}
    done_entries = [{"storyId": "source-1", "status": "done", "fingerprint": "same"}]

    assert match_carried_over(batches, fingerprints, done_entries) == {"current-1"}
    assert match_carried_over(
        batches, fingerprints, [{"storyId": "current-3", "status": "done", "fingerprint": ""}]
    ) == {"current-3"}
    assert match_carried_over(batches, fingerprints, []) == set()
