from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

BACKLOG_TABLES = {
    "epics",
    "user_stories",
    "acceptance_criteria",
    "story_dependencies",
    "agent_tasks",
    "task_dependencies",
    "agent_assignments",
}


def test_backlog_schema_adds_all_entities_as_tables(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 17 in migrations
    assert tables >= BACKLOG_TABLES


def test_backlog_schema_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        phase17_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 17"
        ).fetchone()["total"]

    assert phase17_rows == 1


def test_user_story_is_role_agnostic_and_work_decomposes_into_agent_tasks(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        repo = BacklogRepository(connection)

        project = projects.create_project(name="Backlog", path=tmp_path / "backlog", template_id="other")
        project_id = project["id"]

        epic = repo.create_epic({"projectId": project_id, "title": "Checkout revamp"})
        assert epic["version"] == 1
        assert repo.update_epic(epic["id"], {"status": "active"})["version"] == 2

        story = repo.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Guest checkout",
                "asA": "shopper",
                "iWant": "to check out without an account",
                "soThat": "I can buy faster",
                "businessValue": "high",
                "storyPoints": 5,
            }
        )
        # The story expresses USER VALUE and carries no technical role: it must not be split per role.
        assert "role" not in story
        assert story["asA"] == "shopper"
        assert story["version"] == 1

        # Acceptance criteria are an ordered, story-scoped checklist.
        first_ac = repo.create_acceptance_criterion(
            {"projectId": project_id, "storyId": story["id"], "criterion": "Order completes without login."}
        )
        second_ac = repo.create_acceptance_criterion(
            {"projectId": project_id, "storyId": story["id"], "criterion": "Email receipt is sent."}
        )
        assert (first_ac["sequence"], second_ac["sequence"]) == (1, 2)
        assert [ac["id"] for ac in repo.list_acceptance_criteria(story["id"])] == [
            first_ac["id"],
            second_ac["id"],
        ]

        # ONE story decomposes into MANY role-specific agent tasks (not one story per role).
        roles = ["frontend", "backend", "qa"]
        tasks = [
            repo.create_agent_task(
                {
                    "projectId": project_id,
                    "storyId": story["id"],
                    "title": f"{role} work for guest checkout",
                    "role": role,
                    "estimateHours": 4.0,
                }
            )
            for role in roles
        ]
        story_tasks = repo.list_agent_tasks(story_id=story["id"])
        assert len(story_tasks) == 3
        assert {task["role"] for task in story_tasks} == set(roles)
        assert {task["storyId"] for task in story_tasks} == {story["id"]}
        assert len(repo.list_user_stories(epic_id=epic["id"])) == 1  # no HU duplicated per role
        assert len(repo.list_agent_tasks(project_id=project_id, role="backend")) == 1

        # Agent tasks are versionable work items.
        assert repo.update_agent_task(tasks[0]["id"], {"status": "in_progress"})["version"] == 2

        # Task dependency graph: backend blocks frontend; self-dependency is rejected.
        task_dep = repo.create_task_dependency(
            {
                "projectId": project_id,
                "taskId": tasks[0]["id"],
                "dependsOnTaskId": tasks[1]["id"],
                "type": "blocks",
            }
        )
        assert repo.list_task_dependencies(task_id=tasks[0]["id"])[0]["dependsOnTaskId"] == tasks[1]["id"]
        with pytest.raises(ValueError, match="cannot depend on itself"):
            repo.create_task_dependency(
                {"projectId": project_id, "taskId": tasks[0]["id"], "dependsOnTaskId": tasks[0]["id"]}
            )
        repo.delete_task_dependency(task_dep["id"])
        assert repo.list_task_dependencies(task_id=tasks[0]["id"]) == []

        # Agents are routed to tasks via assignments (the work, not the story).
        assignment = repo.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": tasks[1]["id"],
                "agentId": "backend_engineer",
                "role": "backend",
                "assignedBy": "tech_lead",
            }
        )
        assert assignment["status"] == "proposed"
        released = repo.update_agent_assignment(
            assignment["id"], {"status": "released", "releasedAt": "2026-06-22T00:00:00.000Z"}
        )
        assert released["status"] == "released"
        assert released["releasedAt"] == "2026-06-22T00:00:00.000Z"
        assert [a["id"] for a in repo.list_agent_assignments(agent_id="backend_engineer")] == [
            assignment["id"]
        ]

        # Story dependency graph + project scoping.
        sibling = repo.create_user_story(
            {"projectId": project_id, "epicId": epic["id"], "title": "Saved payment methods"}
        )
        repo.create_story_dependency(
            {"projectId": project_id, "storyId": sibling["id"], "dependsOnStoryId": story["id"]}
        )
        assert repo.list_story_dependencies(story_id=sibling["id"])[0]["dependsOnStoryId"] == story["id"]
        with pytest.raises(ValueError, match="cannot depend on itself"):
            repo.create_story_dependency(
                {"projectId": project_id, "storyId": story["id"], "dependsOnStoryId": story["id"]}
            )

        other_project = projects.create_project(name="Other", path=tmp_path / "other", template_id="other")
        repo.create_epic({"projectId": other_project["id"], "title": "Unrelated epic"})
        assert {item["id"] for item in repo.list_epics(project_id)} == {epic["id"]}
        assert repo.list_user_stories(other_project["id"]) == []
        assert repo.list_agent_tasks(project_id=other_project["id"]) == []
