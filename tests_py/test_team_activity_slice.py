from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.projects.repository import ProjectsRepository


def _client(tmp_path: Path):
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _seed_story(backlog: BacklogRepository, project_id: str) -> str:
    epic = backlog.create_epic({"projectId": project_id, "title": "Activity board"})
    story = backlog.create_user_story(
        {"projectId": project_id, "epicId": epic["id"], "title": "Show team activity"}
    )
    return story["id"]


def test_team_activity_endpoint_is_empty_and_read_only_for_a_fresh_project(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="Team", path=tmp_path / "team", template_id="other"
        )
        # A read with no X-Local-Control-Token header succeeds (200), unlike a write route (403).
        response = client.get(f"/api/v1/projects/{project['id']}/team-activity")
        assert response.status_code == 200
        body = response.json()
        assert body["projectId"] == project["id"]
        assert body["entries"] == []
        assert body["activeCount"] == 0
        assert body["blockedCount"] == 0
        assert body["totalCount"] == 0
        assert body["truncated"] is False
        assert isinstance(body["generatedAt"], str) and body["generatedAt"].endswith("Z")
    finally:
        runtime.close()


def test_team_activity_aggregates_an_active_agent_run_with_all_headline_fields(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        projects = ProjectsRepository(connection)
        agents = AgentsRepository(connection)
        backlog = BacklogRepository(connection)

        project = projects.create_project(name="Team", path=tmp_path / "team", template_id="other")
        project_id = project["id"]
        story_id = _seed_story(backlog, project_id)
        task = backlog.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story_id,
                "title": "Build the activity board",
                "role": "frontend_engineer",
            }
        )
        agents.upsert_agent_profile(
            {
                "id": "agent-frontend",
                "name": "Frontend Dev",
                "role": "frontend_engineer",
                "runtimeMode": "api",
            }
        )
        run = agents.create_agent_run(
            project_id=project_id,
            agent_profile_id="agent-frontend",
            task_id=task["id"],
            input_payload={"prompt": "do work", "token": "SENTINEL_SHOULD_BE_REDACTED"},
            output_payload={"summary": "in progress"},
            status="running",
        )
        agents.record_model_call(
            project_id=project_id,
            provider="anthropic_api",
            model="claude-opus-4-8",
            status="completed",
            agent_run_id=run["id"],
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.12,
        )
        agents.record_agent_tool_call(
            agent_run_id=run["id"],
            tool_name="apply_patch",
            status="completed",
            payload={"path": "src/app.tsx"},
        )
        backlog.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": task["id"],
                "agentId": "agent-frontend",
                "role": "frontend_engineer",
                "assignedBy": "iteration_planner",
                "reviewRequired": True,
                "reviewerAgentId": "agent-qa",
            }
        )

        body = client.get(f"/api/v1/projects/{project_id}/team-activity").json()
        assert body["activeCount"] == 1
        assert body["blockedCount"] == 0
        assert len(body["entries"]) == 1
        entry = body["entries"][0]

        assert entry["id"] == run["id"]
        assert entry["agentName"] == "Frontend Dev"
        assert entry["role"] == "frontend_engineer"
        assert entry["state"] == "active"
        assert entry["status"] == "running"
        assert entry["provider"] == "anthropic_api"
        assert "anthropic_api" in entry["runtime"] and "api" in entry["runtime"]
        assert entry["currentAssignment"]["taskTitle"] == "Build the activity board"
        assert entry["reviewer"]["reviewerAgentId"] == "agent-qa"
        # The artifact is not delivered yet (handoff still pending), so it is honestly absent.
        assert entry["completedArtifact"] is None
        assert entry["costUsd"] == 0.12
        assert entry["costSource"] == "actual"
        assert entry["modelCallCount"] == 1
        assert entry["toolCallCount"] == 1
        assert entry["lowLevelEvents"]["modelCalls"][0]["provider"] == "anthropic_api"
        assert entry["lowLevelEvents"]["toolCalls"][0]["toolName"] == "apply_patch"
        assert entry["durationMs"] is not None and entry["durationMs"] >= 0

        # Redaction runs at write time on secret-named keys, so a value stored under `token`
        # never reaches the developer-details payload.
        assert "SENTINEL_SHOULD_BE_REDACTED" not in json.dumps(entry["developerDetails"])
    finally:
        runtime.close()


def test_team_activity_surfaces_completed_artifact_and_orders_active_before_done(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        projects = ProjectsRepository(connection)
        agents = AgentsRepository(connection)
        backlog = BacklogRepository(connection)

        project = projects.create_project(name="Team", path=tmp_path / "team", template_id="other")
        project_id = project["id"]
        story_id = _seed_story(backlog, project_id)
        agents.upsert_agent_profile(
            {"id": "agent-dev", "name": "Dev", "role": "developer", "runtimeMode": "api"}
        )
        agents.upsert_agent_profile(
            {"id": "agent-qa", "name": "QA", "role": "qa_reviewer", "runtimeMode": "cli"}
        )

        # Active run.
        active_task = backlog.create_agent_task(
            {"projectId": project_id, "storyId": story_id, "title": "Active task", "role": "developer"}
        )
        active_run = agents.create_agent_run(
            project_id=project_id,
            agent_profile_id="agent-dev",
            task_id=active_task["id"],
            input_payload={"prompt": "work"},
            output_payload={},
            status="running",
        )

        # Completed run with a delivered (accepted) artifact and a reviewer.
        done_task = backlog.create_agent_task(
            {"projectId": project_id, "storyId": story_id, "title": "Done task", "role": "qa_reviewer"}
        )
        agents.create_agent_run(
            project_id=project_id,
            agent_profile_id="agent-qa",
            task_id=done_task["id"],
            input_payload={"prompt": "review"},
            output_payload={"verdict": "passed"},
            status="completed",
        )
        assignment = backlog.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": done_task["id"],
                "agentId": "agent-qa",
                "role": "qa_reviewer",
                "assignedBy": "iteration_planner",
                "reviewRequired": True,
                "reviewerAgentId": "agent-security",
            }
        )
        handoff = backlog.list_assignment_handoffs(assignment_id=assignment["id"])[0]
        backlog.update_assignment_handoff(handoff["id"], {"status": "accepted"})

        body = client.get(f"/api/v1/projects/{project_id}/team-activity").json()
        assert next(entry["id"] for entry in body["entries"]) == active_run["id"]  # active first
        assert body["activeCount"] == 1
        assert body["totalCount"] == 2

        done_entry = next(entry for entry in body["entries"] if entry["state"] == "done")
        assert done_entry["completedArtifact"] is not None
        assert done_entry["completedArtifact"]["artifactId"] == assignment["canonicalArtifactId"]
        assert done_entry["reviewer"]["reviewerAgentId"] == "agent-security"
    finally:
        runtime.close()


def test_team_activity_reports_unmeasured_cost_as_not_recorded(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        projects = ProjectsRepository(connection)
        agents = AgentsRepository(connection)
        backlog = BacklogRepository(connection)

        project = projects.create_project(name="Team", path=tmp_path / "team", template_id="other")
        project_id = project["id"]
        story_id = _seed_story(backlog, project_id)
        task = backlog.create_agent_task(
            {"projectId": project_id, "storyId": story_id, "title": "T", "role": "developer"}
        )
        agents.upsert_agent_profile(
            {"id": "agent-dev", "name": "Dev", "role": "developer", "runtimeMode": "api"}
        )
        run = agents.create_agent_run(
            project_id=project_id,
            agent_profile_id="agent-dev",
            task_id=task["id"],
            input_payload={"prompt": "x"},
            output_payload={},
            status="running",
        )
        # A gateway call whose price was never measured persists cost_usd 0.0 with costStatus
        # "unknown"; that must read as "not recorded", not a fabricated real $0.00.
        agents.record_model_call(
            project_id=project_id,
            provider="anthropic_api",
            model="m",
            status="completed",
            agent_run_id=run["id"],
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.0,
            metadata={"costStatus": "unknown"},
        )

        entry = client.get(f"/api/v1/projects/{project_id}/team-activity").json()["entries"][0]
        assert entry["costUsd"] is None
        assert entry["costSource"] is None
    finally:
        runtime.close()


def test_team_activity_redacts_a_secret_in_a_blocked_reason(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        projects = ProjectsRepository(connection)
        agents = AgentsRepository(connection)
        backlog = BacklogRepository(connection)

        project = projects.create_project(name="Team", path=tmp_path / "team", template_id="other")
        project_id = project["id"]
        story_id = _seed_story(backlog, project_id)
        task = backlog.create_agent_task(
            {"projectId": project_id, "storyId": story_id, "title": "T", "role": "developer"}
        )
        agents.upsert_agent_profile(
            {"id": "agent-dev", "name": "Dev", "role": "developer", "runtimeMode": "api"}
        )
        agents.create_agent_run(
            project_id=project_id,
            agent_profile_id="agent-dev",
            task_id=task["id"],
            input_payload={"prompt": "x"},
            output_payload={},
            status="blocked",
        )
        assignment = backlog.create_agent_assignment(
            {
                "projectId": project_id,
                "taskId": task["id"],
                "agentId": "agent-dev",
                "role": "developer",
                "assignedBy": "iteration_planner",
                "reviewRequired": False,
            }
        )
        handoff = backlog.list_assignment_handoffs(assignment_id=assignment["id"])[0]
        # The handoff blocked_reason is persisted raw; the board must redact it before serving.
        backlog.update_assignment_handoff(
            handoff["id"],
            {"status": "blocked", "blockedReason": "Auth failed with sk-supersecret1234567"},
        )

        raw = client.get(f"/api/v1/projects/{project_id}/team-activity").text
        assert "sk-supersecret1234567" not in raw
        entry = client.get(f"/api/v1/projects/{project_id}/team-activity").json()["entries"][0]
        assert entry["blockedReason"] is not None
        assert "[redacted]" in entry["blockedReason"]
    finally:
        runtime.close()


def test_team_activity_is_scoped_to_its_project(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        projects = ProjectsRepository(connection)
        agents = AgentsRepository(connection)
        backlog = BacklogRepository(connection)

        project = projects.create_project(name="A", path=tmp_path / "a", template_id="other")
        other = projects.create_project(name="B", path=tmp_path / "b", template_id="other")
        agents.upsert_agent_profile({"id": "agent-x", "name": "X", "role": "developer", "runtimeMode": "api"})
        story_id = _seed_story(backlog, other["id"])
        task = backlog.create_agent_task(
            {"projectId": other["id"], "storyId": story_id, "title": "Other task", "role": "developer"}
        )
        other_run = agents.create_agent_run(
            project_id=other["id"],
            agent_profile_id="agent-x",
            task_id=task["id"],
            input_payload={"prompt": "work"},
            output_payload={},
            status="running",
        )

        empty = client.get(f"/api/v1/projects/{project['id']}/team-activity").json()
        assert empty["entries"] == []

        populated = client.get(f"/api/v1/projects/{other['id']}/team-activity").json()
        assert [entry["id"] for entry in populated["entries"]] == [other_run["id"]]
    finally:
        runtime.close()
