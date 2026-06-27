from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.self_improvement.coordinator import SelfImprovementCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.evidence_helpers import real_qa_evidence_fields


def _source_project(connection, tmp_path: Path, name: str = "aido-runtime") -> dict:
    source_path = tmp_path / name
    source_path.mkdir(parents=True, exist_ok=True)
    (source_path / "README.md").write_text("runtime source\n", encoding="utf-8")
    return ProjectsRepository(connection).create_project(
        name="AIDO Runtime",
        path=source_path,
        template_id="other",
        source="runtime",
    )


def _evidence(connection, project_id: str) -> dict:
    qa_fields = real_qa_evidence_fields()
    return EvidenceRepository(connection).create_evidence_package(
        project_id=project_id,
        workflow_run_id=None,
        task_id="self-improvement-test",
        test_plan="Self-improvement evidence",
        acceptance_checklist=["Evidence is attached before promotion."],
        qa_verdict="passed",
        evidence_source=qa_fields["evidenceSource"],
        test_results=qa_fields["testResults"],
        tool_calls=qa_fields["toolCalls"],
        policy_decisions=qa_fields["policyDecisions"],
        artifacts=qa_fields["artifacts"],
        hashes=qa_fields["hashes"],
    )


def _client(tmp_path: Path):
    sys.modules["faiss"] = None

    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def test_self_improvement_proposal_creates_dedicated_project_goal_story_workspace_and_pr_workflow(
    tmp_path: Path,
) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        source = _source_project(connection, tmp_path)
        coordinator = SelfImprovementCoordinator(connection, root=tmp_path)

        result = coordinator.propose_change(
            source_project_id=source["id"],
            title="Harden global lesson promotion",
            summary="Global lessons must not be promoted without evidence and explicit approval.",
            proposed_by="operator",
            qa_commands=[["uv", "run", "pytest", "tests_py/test_self_improvement.py", "-q"]],
            target_paths=["local_control_center/self_improvement"],
        )

        self_project = result["selfImprovementProject"]
        proposal = result["proposal"]
        goal = result["goal"]
        story = result["story"]
        task = result["task"]
        workspace = result["workspace"]
        workflow = result["workflow"]

        assert self_project["name"] == "AIDO Self-Improvement"
        assert self_project["id"] != source["id"]
        assert self_project["path"] != source["path"]
        assert self_project["metadata"]["purpose"] == "aido_self_improvement"

        assert proposal["selfProjectId"] == self_project["id"]
        assert proposal["sourceProjectId"] == source["id"]
        assert proposal["status"] == "queued_for_pr"
        assert proposal["goalLoopId"] == goal["id"]
        assert proposal["storyId"] == story["id"]
        assert proposal["taskId"] == task["id"]
        assert proposal["workspaceId"] == workspace["id"]
        assert proposal["workflowId"] == workflow["id"]

        assert goal["projectId"] == self_project["id"]
        assert goal["context"]["selfImprovement"]["sourceProjectId"] == source["id"]
        assert story["projectId"] == self_project["id"]
        assert story["metadata"]["proposalId"] == proposal["id"]
        assert task["projectId"] == self_project["id"]
        assert task["metadata"]["qaCommands"] == [
            ["uv", "run", "pytest", "tests_py/test_self_improvement.py", "-q"]
        ]

        assert workspace["projectId"] == source["id"]
        assert workspace["taskId"] == task["id"]
        assert workspace["path"] != source["path"]
        assert workspace["metadata"]["workspaceManifest"]["sourcePath"] == source["path"]
        assert workspace["metadata"]["reason"] == "AIDO self-improvement proposal workspace."
        Path(workspace["path"], "README.md").write_text("changed in isolation\n", encoding="utf-8")
        assert Path(source["path"], "README.md").read_text(encoding="utf-8") == "runtime source\n"

        assert workflow["projectId"] == source["id"]
        assert workflow["kind"] == "issue_to_pr"
        assert workflow["status"] == "queued"
        assert workflow["metadata"]["selfImprovement"]["proposalId"] == proposal["id"]
        assert workflow["metadata"]["selfImprovement"]["autoExecute"] is False
        assert workflow["metadata"]["issueToPr"]["createPullRequest"] is True
        assert workflow["metadata"]["issueToPr"]["qaCommands"] == task["metadata"]["qaCommands"]


def test_global_lesson_requires_evidence_and_approved_promotion_action(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        source = _source_project(connection, tmp_path)
        evidence = _evidence(connection, source["id"])
        coordinator = SelfImprovementCoordinator(connection, root=tmp_path)

        with pytest.raises(ValueError, match="Global lessons require at least one evidence package"):
            coordinator.record_lesson(
                source_project_id=source["id"],
                scope="global",
                title="Never promote unverified global lessons",
                lesson="A lesson without evidence must stay local.",
                evidence_package_ids=[],
                proposed_by="operator",
            )

        result = coordinator.record_lesson(
            source_project_id=source["id"],
            scope="global",
            title="Never promote unverified global lessons",
            lesson="Global lessons are reusable only after evidence-backed approval.",
            evidence_package_ids=[evidence["id"]],
            proposed_by="operator",
        )
        lesson = result["lesson"]
        job = result["promotionJob"]
        action = result["promotionActionRequest"]

        assert lesson["status"] == "pending_promotion"
        assert lesson["promotionStatus"] == "approval_required"
        assert lesson["evidencePackageIds"] == [evidence["id"]]
        assert job["status"] == "approval_required"
        assert action["status"] == "pending"
        assert action["actionType"] == "self_improvement.promote_global_lesson"
        assert action["evidenceRefs"] == [evidence["id"]]

        with pytest.raises(ValueError, match="approved promotion action request"):
            coordinator.promote_global_lesson(
                lesson["id"],
                actor="operator",
                reason="Trying to promote before approval.",
            )

        JobsRepository(connection).approve_action(
            job["id"],
            action["id"],
            reason="Evidence reviewed; approve global lesson promotion.",
            actor="operator",
        )
        promoted = coordinator.promote_global_lesson(
            lesson["id"],
            actor="operator",
            reason="Evidence reviewed; promote globally.",
        )

        assert promoted["lesson"]["status"] == "promoted"
        assert promoted["lesson"]["promotionStatus"] == "promoted"
        assert promoted["lesson"]["promotedBy"] == "operator"
        assert promoted["lesson"]["approvedActionRequestId"] == action["id"]
        assert promoted["auditEvent"]["action"] == "self_improvement.lesson.promote"


def test_performance_record_requires_evidence_and_links_to_proposal(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        source = _source_project(connection, tmp_path)
        evidence = _evidence(connection, source["id"])
        coordinator = SelfImprovementCoordinator(connection, root=tmp_path)
        proposal = coordinator.propose_change(
            source_project_id=source["id"],
            title="Reduce quality gate latency",
            summary="Track whether the self-improvement workflow gets faster.",
            proposed_by="operator",
            qa_commands=[["uv", "run", "pytest", "tests_py/test_self_improvement.py", "-q"]],
        )["proposal"]

        with pytest.raises(ValueError, match="Performance records require evidence"):
            coordinator.record_performance(
                source_project_id=source["id"],
                proposal_id=proposal["id"],
                metric_name="quality_gate_duration",
                value=42.0,
                unit="seconds",
                evidence_package_id="",
            )

        record = coordinator.record_performance(
            source_project_id=source["id"],
            proposal_id=proposal["id"],
            metric_name="quality_gate_duration",
            value=42.0,
            unit="seconds",
            evidence_package_id=evidence["id"],
            baseline_value=60.0,
            target_value=30.0,
            recorded_by="operator",
        )

        assert record["performanceRecord"]["proposalId"] == proposal["id"]
        assert record["performanceRecord"]["metricName"] == "quality_gate_duration"
        assert record["performanceRecord"]["value"] == 42.0
        assert record["performanceRecord"]["evidencePackageId"] == evidence["id"]
        assert record["auditEvent"]["action"] == "self_improvement.performance.record"


def test_self_improvement_api_is_write_guarded_and_exposes_records(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        connection = runtime.connection
        source = _source_project(connection, tmp_path)
        evidence = _evidence(connection, source["id"])
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token}
        proposal_payload = {
            "sourceProjectId": source["id"],
            "title": "Track self-improvement work",
            "summary": "Proposals must be converted to auditable work.",
            "qaCommands": [["uv", "run", "pytest", "tests_py/test_self_improvement.py", "-q"]],
        }

        assert client.post("/api/v1/self-improvement/proposals", json=proposal_payload).status_code == 403
        proposed = client.post(
            "/api/v1/self-improvement/proposals",
            json=proposal_payload,
            headers=headers,
        )
        assert proposed.status_code == 201
        proposed_body = proposed.json()
        proposal_id = proposed_body["proposal"]["id"]
        assert proposed_body["workspace"]["path"] != source["path"]
        assert proposed_body["workflow"]["kind"] == "issue_to_pr"

        rejected = client.post(
            "/api/v1/self-improvement/lessons",
            json={
                "sourceProjectId": source["id"],
                "scope": "global",
                "title": "Missing evidence",
                "lesson": "This must be rejected.",
                "evidencePackageIds": [],
            },
            headers=headers,
        )
        assert rejected.status_code == 422

        lesson = client.post(
            "/api/v1/self-improvement/lessons",
            json={
                "sourceProjectId": source["id"],
                "scope": "global",
                "title": "Evidence before global promotion",
                "lesson": "Reusable lessons need verifiable evidence and approval.",
                "evidencePackageIds": [evidence["id"]],
            },
            headers=headers,
        )
        assert lesson.status_code == 201
        assert lesson.json()["lesson"]["promotionStatus"] == "approval_required"

        performance = client.post(
            "/api/v1/self-improvement/performance-records",
            json={
                "sourceProjectId": source["id"],
                "proposalId": proposal_id,
                "metricName": "quality_gate_duration",
                "value": 42.0,
                "unit": "seconds",
                "evidencePackageId": evidence["id"],
            },
            headers=headers,
        )
        assert performance.status_code == 201

        state = client.get("/api/v1/self-improvement").json()
        assert [item["id"] for item in state["proposals"]] == [proposal_id]
        assert len(state["lessons"]) == 1
        assert len(state["performanceRecords"]) == 1
    finally:
        runtime.close()


def test_self_improvement_state_read_does_not_create_project_or_workspace(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        response = client.get("/api/v1/self-improvement")

        assert response.status_code == 200
        body = response.json()
        assert body["selfImprovementProject"] is None
        assert body["proposals"] == []
        assert body["lessons"] == []
        assert body["performanceRecords"] == []
        projects = ProjectsRepository(runtime.connection).list_projects()
        assert not any(project["metadata"].get("purpose") == "aido_self_improvement" for project in projects)
        assert not (tmp_path / ".tmp" / "aido-self-improvement").exists()
    finally:
        runtime.close()
