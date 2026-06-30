from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
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
    "assignment_handoffs",
    "assignment_reviews",
    "assignment_conflicts",
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
                "acceptanceCriteria": ["Order completes without login."],
            }
        )
        # The story expresses USER VALUE and carries no technical role: it must not be split per role.
        assert "role" not in story
        assert story["asA"] == "shopper"
        assert story["version"] == 1

        # Acceptance criteria are an ordered, story-scoped checklist.
        first_ac = repo.list_acceptance_criteria(story["id"])[0]
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

        with pytest.raises(ValueError, match="User stories must describe user value"):
            repo.create_user_story(
                {
                    "projectId": project_id,
                    "epicId": epic["id"],
                    "title": "Backend story",
                    "role": "backend_engineer",
                    "acceptanceCriteria": ["A technical role is rejected."],
                }
            )
        with pytest.raises(ValueError, match="requires at least one acceptance criterion"):
            repo.create_user_story(
                {
                    "projectId": project_id,
                    "epicId": epic["id"],
                    "title": "Underspecified story",
                }
            )
        with pytest.raises(KeyError, match="User story not found"):
            repo.create_agent_task(
                {
                    "projectId": project_id,
                    "storyId": "user-story-missing",
                    "title": "Orphan task",
                    "role": "backend_engineer",
                }
            )

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
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Saved payment methods",
                "acceptanceCriteria": ["Saved payment methods are visible to returning shoppers."],
            }
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


def test_agent_assignment_creates_structured_artifact_handoff_and_review_contract(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        repo = BacklogRepository(connection)
        artifacts = EvidenceRepository(connection)

        project = projects.create_project(name="Collab", path=tmp_path / "collab", template_id="other")
        project_id = project["id"]
        epic = repo.create_epic({"projectId": project_id, "title": "Checkout"})
        story = repo.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Pay",
                "acceptanceCriteria": ["The payment flow can be completed."],
            }
        )
        task = repo.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "Backend checkout work",
                "role": "backend_engineer",
            }
        )
        input_schema = {"type": "object", "required": ["storyId", "taskId"]}
        output_schema = {"type": "object", "required": ["artifactId", "summary"]}

        assignment = repo.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": task["id"],
                "agentId": "agent-backend",
                "role": "backend_engineer",
                "assignedBy": "iteration_planner",
                "inputSchema": input_schema,
                "outputSchema": output_schema,
                "reviewRequired": True,
                "reviewerAgentId": "agent-qa",
            }
        )

        assert assignment["inputSchema"] == input_schema
        assert assignment["outputSchema"] == output_schema
        assert assignment["canonicalArtifactId"].startswith("artifact-")
        assert assignment["handoffId"].startswith("assignment-handoff-")
        assert assignment["reviewRequired"] is True

        artifact = artifacts.get_artifact_by_id(assignment["canonicalArtifactId"])
        assert artifact["kind"] == "generic_artifact"
        assert artifact["metadata"]["artifactContract"] == "agent_assignment_canonical"
        assert artifact["metadata"]["assignmentId"] == assignment["id"]
        assert artifact["metadata"]["inputSchema"] == input_schema
        assert artifact["metadata"]["outputSchema"] == output_schema

        handoffs = repo.list_assignment_handoffs(assignment_id=assignment["id"])
        assert len(handoffs) == 1
        assert handoffs[0]["artifactId"] == assignment["canonicalArtifactId"]
        assert handoffs[0]["status"] == "pending"
        assert handoffs[0]["fromAgentId"] == "iteration_planner"
        assert handoffs[0]["toAgentId"] == "agent-backend"

        reviews = repo.list_assignment_reviews(assignment_id=assignment["id"])
        assert len(reviews) == 1
        assert reviews[0]["handoffId"] == assignment["handoffId"]
        assert reviews[0]["reviewerAgentId"] == "agent-qa"
        assert reviews[0]["policyRequired"] is True
        assert reviews[0]["status"] == "pending"

        with pytest.raises(ValueError, match="free-form prompt"):
            repo.create_agent_assignment(
                {
                    "projectId": project_id,
                    "taskId": task["id"],
                    "agentId": "agent-backend-2",
                    "role": "backend_engineer",
                    "assignedBy": "operator",
                    "prompt": "Read the shared prompt and do whatever is needed.",
                }
            )


def test_downstream_assignment_cannot_begin_until_upstream_collaboration_is_resolved(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        projects = ProjectsRepository(connection)
        repo = BacklogRepository(connection)

        project = projects.create_project(name="Gate", path=tmp_path / "gate", template_id="other")
        project_id = project["id"]
        epic = repo.create_epic({"projectId": project_id, "title": "Checkout"})
        story = repo.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Pay",
                "acceptanceCriteria": ["The payment flow can be completed."],
            }
        )
        backend_task = repo.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "Backend checkout work",
                "role": "backend_engineer",
            }
        )
        qa_task = repo.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "QA checkout validation",
                "role": "qa",
            }
        )
        repo.create_task_dependency(
            {
                "projectId": project_id,
                "taskId": qa_task["id"],
                "dependsOnTaskId": backend_task["id"],
            }
        )
        upstream = repo.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": backend_task["id"],
                "agentId": "agent-backend",
                "role": "backend_engineer",
                "assignedBy": "iteration_planner",
                "reviewRequired": True,
                "reviewerAgentId": "agent-qa-reviewer",
            }
        )
        downstream = repo.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": qa_task["id"],
                "agentId": "agent-qa",
                "role": "qa",
                "assignedBy": "iteration_planner",
                "reviewRequired": False,
            }
        )

        with pytest.raises(ValueError, match="upstream handoff"):
            repo.update_agent_assignment(downstream["id"], {"status": "in_progress"})

        repo.update_assignment_handoff(upstream["handoffId"], {"status": "accepted"})
        with pytest.raises(ValueError, match="required review"):
            repo.update_agent_assignment(downstream["id"], {"status": "in_progress"})

        review = repo.list_assignment_reviews(assignment_id=upstream["id"])[0]
        recorded_review = repo.record_assignment_review(
            review["id"],
            {
                "status": "approved",
                "decision": "approved",
                "findings": [{"severity": "info", "message": "Covered by focused QA."}],
                "reviewerAgentId": "agent-qa-reviewer",
            },
        )
        assert recorded_review["findings"][0]["message"] == "Covered by focused QA."

        conflict = repo.create_assignment_conflict(
            {
                "projectId": project_id,
                "assignmentId": upstream["id"],
                "handoffId": upstream["handoffId"],
                "raisedBy": "agent-qa-reviewer",
                "disagreement": "Reviewer and implementer disagree on the validation boundary.",
            }
        )
        with pytest.raises(ValueError, match="unresolved conflict"):
            repo.update_agent_assignment(downstream["id"], {"status": "in_progress"})

        resolved = repo.resolve_assignment_conflict(
            conflict["id"],
            {
                "finalResolution": "Validation boundary accepted after adding focused QA evidence.",
                "resolvedBy": "technical_lead",
            },
        )
        assert resolved["status"] == "resolved"
        assert resolved["finalResolution"] == "Validation boundary accepted after adding focused QA evidence."

        started = repo.update_agent_assignment(downstream["id"], {"status": "in_progress"})
        assert started["status"] == "in_progress"
