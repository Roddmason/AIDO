from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_control_center.backlog.iteration_planner import IterationPlanner, IterationPlannerError
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _ready_stories(backlog: BacklogRepository, project_id: str, epic_id: str) -> tuple[dict, dict]:
    first = backlog.create_user_story(
        {
            "projectId": project_id,
            "epicId": epic_id,
            "title": "Guest checkout",
            "asA": "shopper",
            "iWant": "to complete payment without creating an account",  # 'payment' -> security-sensitive
            "soThat": "I buy faster",
            "storyPoints": 3,
        }
    )
    first = backlog.update_user_story(first["id"], {"status": "ready"})
    second = backlog.create_user_story(
        {
            "projectId": project_id,
            "epicId": epic_id,
            "title": "Order history",
            "asA": "shopper",
            "iWant": "to see my past orders",
            "soThat": "I can reorder",
            "storyPoints": 2,
        }
    )
    second = backlog.update_user_story(second["id"], {"status": "ready"})
    return first, second


def _payload(stories: list[dict], **overrides: Any) -> dict[str, Any]:
    base = {
        "briefId": "product-brief-test",
        "brief": {"status": "approved", "title": "Checkout"},
        "stories": stories,
        "architecture": [{"title": "Auth boundary", "severity": "medium"}],
        "availableAgents": [
            {"agentId": "agent-be", "role": "backend_engineer"},
            {"agentId": "agent-fe", "role": "frontend_engineer"},
            {"agentId": "agent-qa", "role": "qa"},
        ],
        "executableRuntimes": ["codex_cli"],
        "budgets": {"maxCostUsd": 10.0},
    }
    base.update(overrides)
    return base


def test_iteration_schema_adds_table_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase20_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 20"
        ).fetchone()["total"]

    assert "iterations" in tables
    assert phase20_rows == 1


def test_planner_produces_full_plan_and_persists_the_dag(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Iter", path=tmp_path / "iter", template_id="other"
        )
        backlog = BacklogRepository(connection)
        epic = backlog.create_epic({"projectId": project["id"], "title": "Checkout"})
        first, second = _ready_stories(backlog, project["id"], epic["id"])
        payload = _payload(
            [first, second], storyDependencies=[{"storyId": second["id"], "dependsOnStoryId": first["id"]}]
        )

        result = IterationPlanner(backlog).plan(project_id=project["id"], payload=payload)

        # Iteration + estimated cost ALWAYS marked as estimated.
        iteration = result["iteration"]
        assert iteration["status"] == "planned"
        assert iteration["storyIds"] == [first["id"], second["id"]]
        assert result["estimatedCost"]["estimated"] is True
        assert result["estimatedCost"]["amountUsd"] == pytest.approx(1.44)  # 6 tasks * 0.24
        assert result["estimatedCost"]["overBudget"] is False

        # Task DAG: one task per role per story (2 x 3), assignments by role.
        tasks = result["taskDag"]["tasks"]
        assert len(tasks) == 6
        assert {t["role"] for t in tasks} == {"backend_engineer", "frontend_engineer", "qa"}
        qa_first = next(t for t in tasks if t["role"] == "qa" and t["storyId"] == first["id"])
        assert len(qa_first["dependsOn"]) == 2  # qa depends on backend + frontend of the same story
        backend_second = next(
            t for t in tasks if t["role"] == "backend_engineer" and t["storyId"] == second["id"]
        )
        assert len(backend_second["dependsOn"]) == 1  # inter-story dependency on the first story
        assert set(result["assignmentsByRole"]) == {"backend_engineer", "frontend_engineer", "qa"}

        # Workspace strategy isolates parallel work across the two stories.
        assert result["workspaceStrategy"]["strategy"] == "worktree_per_task"
        assert result["workspaceStrategy"]["isolation"] is True

        # Quality gates + risk-based security gates (the payment story raises the risk).
        assert {g["id"] for g in result["qualityGates"]} >= {
            "lint",
            "typecheck",
            "unit_tests",
            "build",
            "coverage",
            "integration_tests",
        }
        security_ids = {g["id"] for g in result["securityGates"]}
        assert "secret_scan" in security_ids  # baseline
        assert {"dependency_audit", "sast", "threat_review"} <= security_ids  # risk-based

        # Persistence: DAG materialized in the backlog and tagged with the iteration.
        persisted_tasks = backlog.list_agent_tasks(project_id=project["id"])
        assert len(persisted_tasks) == 6
        assert all(task["metadata"]["iterationId"] == iteration["id"] for task in persisted_tasks)
        assert backlog.list_iterations(project["id"])[0]["id"] == iteration["id"]
        assert len(result["assignments"]) == 6
        assert backlog.list_task_dependencies(task_id=qa_first["taskId"])  # qa has persisted deps


def test_planner_flags_over_budget_estimate(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Budget", path=tmp_path / "budget", template_id="other"
        )
        backlog = BacklogRepository(connection)
        epic = backlog.create_epic({"projectId": project["id"], "title": "X"})
        first, second = _ready_stories(backlog, project["id"], epic["id"])
        payload = _payload([first, second], budgets={"maxCostUsd": 0.5})

        result = IterationPlanner(backlog).plan(project_id=project["id"], payload=payload)
        assert result["estimatedCost"]["estimated"] is True
        assert result["estimatedCost"]["amountUsd"] == pytest.approx(1.44)
        assert result["estimatedCost"]["overBudget"] is True  # 1.44 > 0.5


def test_planner_uses_runtimes_for_workspace_strategy_and_persists_them(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Rt", path=tmp_path / "rt", template_id="other"
        )
        backlog = BacklogRepository(connection)
        epic = backlog.create_epic({"projectId": project["id"], "title": "X"})
        first, second = _ready_stories(backlog, project["id"], epic["id"])
        # Model-only runtimes cannot drive isolated git worktrees, so agents share one workspace.
        payload = _payload([first, second], executableRuntimes=["openai_compatible"])

        result = IterationPlanner(backlog).plan(project_id=project["id"], payload=payload)
        assert result["workspaceStrategy"]["strategy"] == "single_shared_workspace"
        assert result["workspaceStrategy"]["isolation"] is False
        assert result["iteration"]["runtimes"] == ["openai_compatible"]


def test_planner_ignores_self_referential_story_dependency(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="SelfDep", path=tmp_path / "selfdep", template_id="other"
        )
        backlog = BacklogRepository(connection)
        epic = backlog.create_epic({"projectId": project["id"], "title": "X"})
        first, second = _ready_stories(backlog, project["id"], epic["id"])
        payload = _payload(
            [first, second],
            storyDependencies=[{"storyId": first["id"], "dependsOnStoryId": first["id"]}],
        )

        result = IterationPlanner(backlog).plan(project_id=project["id"], payload=payload)
        tasks = result["taskDag"]["tasks"]
        # The self-dependency is skipped: the tier-0 backend task keeps zero deps (no cycle introduced).
        backend_first = next(
            t for t in tasks if t["role"] == "backend_engineer" and t["storyId"] == first["id"]
        )
        assert backend_first["dependsOn"] == []


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda p: p.update(brief={"status": "draft", "title": "X"}), "approved"),
        (lambda p: p.update(stories=[]), "ready story"),
        (lambda p: p["stories"][0].update(status="draft"), "is not ready"),
        (lambda p: p.update(availableAgents=[]), "available agent"),
        (lambda p: p.update(executableRuntimes=[]), "executable runtime"),
        (lambda p: p.update(budgets={"currency": "USD"}), "maxCostUsd"),
    ],
)
def test_planner_rejects_missing_or_invalid_inputs(tmp_path: Path, mutate, fragment: str) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Bad", path=tmp_path / "bad", template_id="other"
        )
        backlog = BacklogRepository(connection)
        epic = backlog.create_epic({"projectId": project["id"], "title": "X"})
        first, second = _ready_stories(backlog, project["id"], epic["id"])
        payload = _payload([first, second])
        mutate(payload)

        with pytest.raises(IterationPlannerError, match=fragment):
            IterationPlanner(backlog).plan(project_id=project["id"], payload=payload)
        # Nothing is persisted when validation fails.
        assert backlog.list_iterations(project["id"]) == []
        assert backlog.list_agent_tasks(project_id=project["id"]) == []
