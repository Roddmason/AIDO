from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository

EMPTY_LOOP_STATE = {
    "loops": [],
    "transitions": [],
    "questions": [],
    "brief": None,
    "assumptions": [],
    "decisions": [],
    "epics": [],
    "stories": [],
    "tasks": [],
    "assignments": [],
    "assignmentHandoffs": [],
    "assignmentReviews": [],
    "assignmentConflicts": [],
    "iterations": [],
}


def _client(tmp_path: Path):
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def test_product_loop_endpoint_returns_empty_collections_for_a_fresh_project(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="Loop", path=tmp_path / "loop", template_id="other"
        )
        response = client.get(f"/api/v1/projects/{project['id']}/product-loop")

        assert response.status_code == 200
        assert response.json() == EMPTY_LOOP_STATE
    finally:
        runtime.close()


def test_product_loop_endpoint_is_read_only_and_takes_no_token(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="Loop", path=tmp_path / "loop", template_id="other"
        )
        # A read with no X-Local-Control-Token header succeeds (200), unlike a write route (403).
        response = client.get(f"/api/v1/projects/{project['id']}/product-loop")
        assert response.status_code == 200
    finally:
        runtime.close()


def test_product_loop_endpoint_aggregates_real_loop_state_scoped_to_the_project(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        projects = ProjectsRepository(connection)
        discovery = ProductDiscoveryRepository(connection)
        backlog = BacklogRepository(connection)

        project = projects.create_project(name="Loop", path=tmp_path / "loop", template_id="other")
        other = projects.create_project(name="Other", path=tmp_path / "other", template_id="other")
        project_id = project["id"]

        loop = ProductLoopCoordinator(connection).start(project_id=project_id, title="Onboarding")
        initiative = discovery.create_initiative(
            {"projectId": project_id, "title": "Self-serve onboarding", "summary": "Reduce time-to-value."}
        )
        session = discovery.create_discovery_session(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "title": "Kickoff",
                "objective": "Map the onboarding journey.",
                "facilitator": "product_owner",
            }
        )
        question = discovery.create_clarification_question(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "sessionId": session["id"],
                "question": "Which segment activates fastest?",
                "askedBy": "product_owner",
            }
        )
        brief = discovery.upsert_product_brief(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "title": "Onboarding brief",
                "summary": "Cut activation friction.",
                "problemStatement": "Users stall at manual setup.",
                "goals": ["Reduce setup steps"],
                "targetUsers": ["SMB admins"],
                "successMetrics": ["activation_rate"],
                "scope": "Guided setup wizard.",
                "outOfScope": "Enterprise SSO.",
                "changeSummary": "Initial draft.",
                "authoredBy": "product_owner",
            }
        )
        discovery.create_assumption(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "briefId": brief["id"],
                "sourceQuestionId": question["id"],
                "statement": "SMB admins prefer guided setup.",
                "confidence": "medium",
            }
        )
        discovery.create_product_decision(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "briefId": brief["id"],
                "title": "Ship guided setup wizard",
                "status": "accepted",
                "context": "Activation stalls at manual config.",
                "decision": "Build a guided wizard for SMB onboarding.",
                "rationale": "Highest-leverage activation lever.",
                "consequences": ["Wizard maintenance cost"],
                "linkedAssumptionIds": [],
                "linkedQuestionIds": [question["id"]],
                "decidedBy": "product_owner",
            }
        )
        epic = backlog.create_epic({"projectId": project_id, "title": "Checkout revamp"})
        story = backlog.create_user_story(
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
        task = backlog.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "frontend work for guest checkout",
                "role": "frontend",
                "estimateHours": 4.0,
            }
        )
        assignment = backlog.create_agent_assignment(
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
        backlog.create_iteration(
            {
                "projectId": project_id,
                "briefId": brief["id"],
                "title": "Iteration 1",
                "goal": "Ship the wizard",
                "workspaceStrategy": "worktree_per_task",
            }
        )

        # A second project's loop must not leak into this project's view.
        other_loop = ProductLoopCoordinator(connection).start(project_id=other["id"], title="Unrelated")

        response = client.get(f"/api/v1/projects/{project_id}/product-loop")
        assert response.status_code == 200
        body = response.json()

        assert [item["id"] for item in body["loops"]] == [loop["id"]]
        assert body["loops"][0]["state"] == "goal_received"
        assert isinstance(body["transitions"], list)  # transitions of the active loop, if any
        assert [item["id"] for item in body["questions"]] == [question["id"]]
        assert body["brief"]["id"] == brief["id"]
        assert body["brief"]["goals"] == ["Reduce setup steps"]
        assert len(body["assumptions"]) == 1
        assert body["assumptions"][0]["confidence"] == "medium"
        assert len(body["decisions"]) == 1
        assert body["decisions"][0]["status"] == "accepted"
        assert body["decisions"][0]["linkedQuestionIds"] == [question["id"]]
        assert [item["id"] for item in body["epics"]] == [epic["id"]]
        assert [item["id"] for item in body["stories"]] == [story["id"]]
        assert body["stories"][0]["asA"] == "shopper"
        assert body["stories"][0]["storyPoints"] == 5
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["role"] == "frontend"
        assert [item["id"] for item in body["assignments"]] == [assignment["id"]]
        assert body["assignments"][0]["canonicalArtifactId"] == assignment["canonicalArtifactId"]
        assert body["assignments"][0]["inputSchema"]["type"] == "object"
        assert [item["assignmentId"] for item in body["assignmentHandoffs"]] == [assignment["id"]]
        assert body["assignmentHandoffs"][0]["artifactId"] == assignment["canonicalArtifactId"]
        assert [item["assignmentId"] for item in body["assignmentReviews"]] == [assignment["id"]]
        assert body["assignmentReviews"][0]["status"] == "pending"
        assert body["assignmentConflicts"] == []
        assert len(body["iterations"]) == 1
        assert body["iterations"][0]["workspaceStrategy"] == "worktree_per_task"

        # Project scoping: the other project's loop is absent from this project's view.
        assert other_loop["id"] not in {item["id"] for item in body["loops"]}

        # The other project's view is empty of this project's backlog/discovery data.
        other_body = client.get(f"/api/v1/projects/{other['id']}/product-loop").json()
        assert other_body["questions"] == []
        assert other_body["brief"] is None
        assert other_body["epics"] == []
        assert other_body["assignments"] == []
        assert other_body["assignmentHandoffs"] == []
        assert other_body["assignmentReviews"] == []
        assert other_body["assignmentConflicts"] == []
        assert [item["id"] for item in other_body["loops"]] == [other_loop["id"]]
    finally:
        runtime.close()


def test_start_and_transition_product_loop_mutations(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="Loop", path=tmp_path / "loop", template_id="other"
        )
        project_id = project["id"]
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token}

        # Both mutations are write-guarded: no token is a 403.
        assert (
            client.post(
                f"/api/v1/projects/{project_id}/product-loop", json={"title": "Onboarding"}
            ).status_code
            == 403
        )

        # Start a loop in goal_received and surface the allowed next states.
        started = client.post(
            f"/api/v1/projects/{project_id}/product-loop", json={"title": "Onboarding"}, headers=headers
        )
        assert started.status_code == 201
        started_body = started.json()
        assert started_body["loop"]["state"] == "goal_received"
        assert started_body["loop"]["version"] == 1
        assert started_body["resumable"] is True
        assert "discovering" in started_body["allowedNextStates"]
        loop_id = started_body["loop"]["id"]

        # A transition with no token is also a 403.
        assert (
            client.post(
                f"/api/v1/projects/{project_id}/product-loop/{loop_id}/transition",
                json={"toState": "discovering"},
            ).status_code
            == 403
        )

        # Advance to an allowed state; the version increments and the log grows.
        advanced = client.post(
            f"/api/v1/projects/{project_id}/product-loop/{loop_id}/transition",
            json={"toState": "discovering", "reason": "Discovery kicked off."},
            headers=headers,
        )
        assert advanced.status_code == 200
        advanced_body = advanced.json()
        assert advanced_body["loop"]["state"] == "discovering"
        assert advanced_body["loop"]["version"] == 2

        # A transition the FSM forbids from the current state is rejected with 422.
        invalid = client.post(
            f"/api/v1/projects/{project_id}/product-loop/{loop_id}/transition",
            json={"toState": "delivered"},
            headers=headers,
        )
        assert invalid.status_code == 422

        # The read aggregate now reflects the started/advanced loop and its two transitions.
        state = client.get(f"/api/v1/projects/{project_id}/product-loop").json()
        assert [item["id"] for item in state["loops"]] == [loop_id]
        assert len(state["transitions"]) == 2
    finally:
        runtime.close()


def test_transition_unknown_or_cross_project_loop_returns_404(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        projects = ProjectsRepository(runtime.connection)
        project = projects.create_project(name="A", path=tmp_path / "a", template_id="other")
        other = projects.create_project(name="B", path=tmp_path / "b", template_id="other")
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token}

        loop = ProductLoopCoordinator(runtime.connection).start(project_id=project["id"], title="X")

        # Unknown loop id is a 404.
        assert (
            client.post(
                f"/api/v1/projects/{project['id']}/product-loop/does-not-exist/transition",
                json={"toState": "discovering"},
                headers=headers,
            ).status_code
            == 404
        )

        # A real loop addressed under the wrong project is a 404 (scoping guard).
        assert (
            client.post(
                f"/api/v1/projects/{other['id']}/product-loop/{loop['id']}/transition",
                json={"toState": "discovering"},
                headers=headers,
            ).status_code
            == 404
        )
    finally:
        runtime.close()
