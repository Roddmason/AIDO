from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads.repository import ThreadsRepository

EMPTY_LOOP_STATE = {
    "loops": [],
    "transitions": [],
    "feedback": [],
    "questions": [],
    "brief": None,
    "assumptions": [],
    "decisions": [],
    "epics": [],
    "stories": [],
    "acceptanceCriteria": [],
    "storyDependencies": [],
    "tasks": [],
    "taskDependencies": [],
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
                "acceptanceCriteria": ["Guest checkout completes without an account."],
            }
        )
        criterion = backlog.list_acceptance_criteria(story["id"])[0]
        sibling_story = backlog.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Guest email receipt",
                "acceptanceCriteria": ["Receipt is sent after checkout."],
            }
        )
        story_dependency = backlog.create_story_dependency(
            {
                "projectId": project_id,
                "storyId": sibling_story["id"],
                "dependsOnStoryId": story["id"],
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
        qa_task = backlog.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "QA work for guest checkout",
                "role": "qa",
                "estimateHours": 2.0,
            }
        )
        task_dependency = backlog.create_task_dependency(
            {
                "projectId": project_id,
                "taskId": qa_task["id"],
                "dependsOnTaskId": task["id"],
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
        assert body["feedback"] == []
        assert [item["id"] for item in body["questions"]] == [question["id"]]
        assert body["brief"]["id"] == brief["id"]
        assert body["brief"]["goals"] == ["Reduce setup steps"]
        assert len(body["assumptions"]) == 1
        assert body["assumptions"][0]["confidence"] == "medium"
        assert len(body["decisions"]) == 1
        assert body["decisions"][0]["status"] == "accepted"
        assert body["decisions"][0]["linkedQuestionIds"] == [question["id"]]
        assert [item["id"] for item in body["epics"]] == [epic["id"]]
        assert {item["id"] for item in body["stories"]} == {story["id"], sibling_story["id"]}
        guest_story = next(item for item in body["stories"] if item["id"] == story["id"])
        assert guest_story["asA"] == "shopper"
        assert guest_story["storyPoints"] == 5
        assert {item["storyId"] for item in body["acceptanceCriteria"]} == {
            story["id"],
            sibling_story["id"],
        }
        guest_criterion = next(item for item in body["acceptanceCriteria"] if item["id"] == criterion["id"])
        assert guest_criterion["storyId"] == story["id"]
        assert guest_criterion["criterion"] == "Guest checkout completes without an account."
        assert [item["id"] for item in body["storyDependencies"]] == [story_dependency["id"]]
        assert body["storyDependencies"][0]["dependsOnStoryId"] == story["id"]
        assert len(body["tasks"]) == 2
        assert {item["role"] for item in body["tasks"]} == {"frontend", "qa"}
        assert [item["id"] for item in body["taskDependencies"]] == [task_dependency["id"]]
        assert body["taskDependencies"][0]["dependsOnTaskId"] == task["id"]
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
        assert other_body["acceptanceCriteria"] == []
        assert other_body["storyDependencies"] == []
        assert other_body["taskDependencies"] == []
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


def test_feedback_action_endpoint_is_guarded_classifies_and_exposes_trace(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        project = ProjectsRepository(connection).create_project(
            name="Feedback", path=tmp_path / "feedback", template_id="other"
        )
        project_id = project["id"]
        backlog = BacklogRepository(connection)
        epic = backlog.create_epic({"projectId": project_id, "title": "Checkout"})
        story = backlog.create_user_story(
            {
                "projectId": project_id,
                "epicId": epic["id"],
                "title": "Guest checkout",
                "acceptanceCriteria": ["Guest checkout validates payment fields."],
            }
        )
        task = backlog.create_agent_task(
            {
                "projectId": project_id,
                "storyId": story["id"],
                "title": "Fix checkout validation",
                "role": "backend_engineer",
                "status": "completed",
            }
        )
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project_id, title="Feedback loop")
        for state in [
            "discovering",
            "brief_ready",
            "architecture_review",
            "backlog_ready",
            "iteration_planning",
            "executing",
            "qa_running",
            "security_running",
            "quality_review",
            "awaiting_approval",
        ]:
            coordinator.transition(loop["id"], to_state=state)

        token = client.get("/api/v1/security/handshake").json()["token"]
        url = f"/api/v1/projects/{project_id}/product-loop/{loop['id']}/feedback"
        payload = {
            "action": "request_changes",
            "feedback": "Validation needs another pass.",
            "targetType": "task",
            "targetId": task["id"],
        }

        assert client.post(url, json=payload).status_code == 403
        applied = client.post(url, json=payload, headers={"X-Local-Control-Token": token})
        assert applied.status_code == 200
        body = applied.json()
        assert body["feedback"]["action"] == "request_changes"
        assert body["feedback"]["classification"] == "rework_task"
        assert body["loop"]["state"] == "reworking"
        assert body["feedback"]["effects"][0]["type"] == "transition"
        assert body["feedback"]["effects"][0]["transitionId"].startswith("product-loop-transition-")

        aggregate = client.get(f"/api/v1/projects/{project_id}/product-loop").json()
        assert [item["id"] for item in aggregate["feedback"]] == [body["feedback"]["id"]]
        assert aggregate["transitions"][-1]["metadata"]["feedbackId"] == body["feedback"]["id"]
    finally:
        runtime.close()


def test_review_action_approval_delivers_product_loop_feedback(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        project = ProjectsRepository(connection).create_project(
            name="Review approval", path=tmp_path / "review-approval", template_id="other"
        )
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project_id, title="Review approval loop")
        for state in [
            "discovering",
            "brief_ready",
            "architecture_review",
            "backlog_ready",
            "iteration_planning",
            "executing",
            "qa_running",
            "security_running",
            "quality_review",
            "awaiting_approval",
        ]:
            loop = coordinator.transition(loop["id"], to_state=state)
        thread = ThreadsRepository(connection).create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id=project_id,
            title="Review approval thread",
        )
        ThreadsRepository(connection).set_status(thread["id"], "awaiting_approval")
        job = JobsRepository(connection).create_job(
            project_id=project_id,
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload={"loopId": loop["id"]},
        )["job"]
        action = JobsRepository(connection).create_action_request(
            job_id=job["id"],
            project_id=project_id,
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload={"loopId": loop["id"]},
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
        coordinator.repository.update_loop_context(
            loop["id"],
            context={
                **loop["context"],
                "durableRun": {
                    **dict(loop["context"].get("durableRun") or {}),
                    "approval": {"jobId": job["id"], "actionRequestId": action["id"]},
                    "thread": {"projectThreadId": thread["id"]},
                },
            },
        )

        token = client.get("/api/v1/security/handshake").json()["token"]
        approved = client.post(
            f"/api/v1/jobs/{job['id']}/actions/{action['id']}/approve",
            json={"reason": "Evidence reviewed from Review inbox."},
            headers={"X-Local-Control-Token": token},
        )

        assert approved.status_code == 202
        aggregate = client.get(f"/api/v1/projects/{project_id}/product-loop").json()
        assert aggregate["loops"][0]["state"] == "delivered"
        assert aggregate["feedback"][0]["action"] == "accept"
        assert aggregate["feedback"][0]["targetId"] == loop["id"]
        assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "resolved"
    finally:
        runtime.close()


def test_review_action_denial_requests_product_loop_delivery_feedback(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        project = ProjectsRepository(connection).create_project(
            name="Review denial", path=tmp_path / "review-denial", template_id="other"
        )
        project_id = project["id"]
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project_id, title="Review denial loop")
        for state in [
            "discovering",
            "brief_ready",
            "architecture_review",
            "backlog_ready",
            "iteration_planning",
            "executing",
            "qa_running",
            "security_running",
            "quality_review",
            "awaiting_approval",
        ]:
            loop = coordinator.transition(loop["id"], to_state=state)
        thread = ThreadsRepository(connection).create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id=project_id,
            title="Review denial thread",
        )
        ThreadsRepository(connection).set_status(thread["id"], "awaiting_approval")
        job = JobsRepository(connection).create_job(
            project_id=project_id,
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload={"loopId": loop["id"]},
        )["job"]
        action = JobsRepository(connection).create_action_request(
            job_id=job["id"],
            project_id=project_id,
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload={"loopId": loop["id"]},
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
        coordinator.repository.update_loop_context(
            loop["id"],
            context={
                **loop["context"],
                "durableRun": {
                    **dict(loop["context"].get("durableRun") or {}),
                    "approval": {"jobId": job["id"], "actionRequestId": action["id"]},
                    "thread": {"projectThreadId": thread["id"]},
                },
            },
        )

        token = client.get("/api/v1/security/handshake").json()["token"]
        denied = client.post(
            f"/api/v1/jobs/{job['id']}/actions/{action['id']}/deny",
            json={"reason": "Evidence needs a targeted rework decision."},
            headers={"X-Local-Control-Token": token},
        )

        assert denied.status_code == 202
        aggregate = client.get(f"/api/v1/projects/{project_id}/product-loop").json()
        assert aggregate["loops"][0]["state"] == "awaiting_feedback"
        assert aggregate["feedback"][0]["action"] == "request_changes"
        assert aggregate["feedback"][0]["targetType"] == "loop"
        assert aggregate["feedback"][0]["targetId"] == loop["id"]
        assert JobsRepository(connection).get_action_request(action["id"])["status"] == "denied"
        assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "open"
    finally:
        runtime.close()


def test_aido_decide_answers_questions_and_records_product_decisions(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        project = ProjectsRepository(connection).create_project(
            name="AIDO decide", path=tmp_path / "aido-decide", template_id="other"
        )
        project_id = project["id"]
        discovery = ProductDiscoveryRepository(connection)
        initiative = discovery.create_initiative(
            {"projectId": project_id, "title": "Creator payments", "summary": "Enable paid content."}
        )
        loop = ProductLoopCoordinator(connection).start(
            project_id=project_id,
            title="Creator payments",
            initiative_id=initiative["id"],
        )
        question = discovery.create_clarification_question(
            {
                "projectId": project_id,
                "initiativeId": initiative["id"],
                "question": "Which creator segment launches first?",
                "askedBy": "product_owner_agent",
                "metadata": {
                    "category": "users",
                    "whyItMatters": "It changes onboarding and compliance scope.",
                    "blocking": True,
                    "options": ["Solo creators", "Teams"],
                    "recommendation": "Solo creators",
                    "defaultDecision": "Solo creators",
                    "confidence": "medium",
                },
            }
        )
        token = client.get("/api/v1/security/handshake").json()["token"]
        url = f"/api/v1/projects/{project_id}/product-loop/{loop['id']}/aido-decide"

        assert client.post(url, json={"reason": "Use defaults."}).status_code == 403
        response = client.post(
            url, json={"reason": "Use high-confidence defaults."}, headers={"X-Local-Control-Token": token}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["questions"][0]["status"] == "answered"
        assert body["decisions"][0]["status"] == "accepted"
        assert body["decisions"][0]["decision"] == "Solo creators"
        assert body["decisions"][0]["metadata"]["sourceQuestionId"] == question["id"]
        answers = discovery.list_clarification_answers(question["id"])
        assert answers[0]["answer"] == "Solo creators"
        assert answers[0]["answeredBy"] == "aido_decide"
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
