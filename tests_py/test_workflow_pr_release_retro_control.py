from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.shared.serialization import json_loads
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.evidence_helpers import real_qa_evidence_fields


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_app(tmp_path: Path, monkeypatch) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def create_and_start_release_workflow(
    store: ControlPlaneFixture,
    client: TestClient,
    headers: dict[str, str],
    tmp_path: Path,
) -> tuple[dict, dict]:
    project = store.create_project(
        name="Gate Advancement", path=tmp_path / "gate-advancement", template_id="other"
    )
    created = client.post(
        "/api/v1/workflows",
        json={
            "projectId": project["id"],
            "kind": "pr_release_retro",
            "title": "Governed release loop",
            "metadata": {
                "steps": [
                    {"name": "pr_review", "role": "technical_lead", "taskType": "pr_review"},
                    {
                        "name": "release_gate",
                        "role": "release_manager",
                        "taskType": "release_gate",
                        "environment": "production",
                    },
                    {"name": "retro", "role": "technical_lead", "taskType": "retro"},
                ]
            },
        },
        headers=headers,
    )
    assert created.status_code == 201
    started = client.post(
        f"/api/v1/workflows/{created.json()['workflow']['id']}/start",
        json={"reason": "gate advancement test"},
        headers=headers,
    )
    assert started.status_code == 202
    return project, started.json()


def test_workflow_declared_pr_release_retro_gates_create_auditable_controls(
    tmp_path: Path, monkeypatch
) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Release Flow", path=tmp_path / "release-flow", template_id="other")

    created = client.post(
        "/api/v1/workflows",
        json={
            "projectId": project["id"],
            "kind": "pr_release_retro",
            "title": "Release governed control plane changes",
            "metadata": {
                "steps": [
                    {
                        "name": "pr_review",
                        "role": "technical_lead",
                        "taskType": "pr_review",
                        "requiresEvidence": True,
                    },
                    {
                        "name": "release_gate",
                        "role": "release_manager",
                        "taskType": "release_gate",
                        "environment": "production",
                    },
                    {
                        "name": "retro",
                        "role": "technical_lead",
                        "taskType": "retro",
                    },
                ]
            },
        },
        headers=headers,
    )
    assert created.status_code == 201

    started = client.post(
        f"/api/v1/workflows/{created.json()['workflow']['id']}/start",
        json={"reason": "next sprint governed release"},
        headers=headers,
    )
    assert started.status_code == 202
    body = started.json()
    step_names = [step["name"] for step in body["workflowSteps"]]
    assert step_names == ["pr_review", "release_gate", "retro"]

    pr_step = next(step for step in body["workflowSteps"] if step["name"] == "pr_review")
    release_step = next(step for step in body["workflowSteps"] if step["name"] == "release_gate")
    retro_step = next(step for step in body["workflowSteps"] if step["name"] == "retro")
    assert pr_step["metadata"]["gateState"] == "blocked_pending_qa_evidence"
    assert release_step["metadata"]["gateState"] == "blocked_pending_human_approval"
    assert retro_step["metadata"]["gateState"] == "governance_seeded"

    jobs = store.jobs.list_jobs_for_workflow_runs([body["workflowRun"]["id"]])
    release_jobs = [job for job in jobs if job["kind"] == "release.production"]
    assert len(release_jobs) == 1
    assert release_jobs[0]["status"] == "approval_required"
    action_requests = store.jobs.list_action_requests(release_jobs[0]["id"])
    assert action_requests[0]["actionType"] == "job.release.production"
    assert action_requests[0]["status"] == "pending"

    workflow_events = store.connection.execute(
        "SELECT type, payload FROM workflow_events WHERE workflow_run_id = ? ORDER BY created_at ASC",
        (body["workflowRun"]["id"],),
    ).fetchall()
    event_types = {row["type"] for row in workflow_events}
    assert {
        "workflow.gate.pr_review.evidence_required",
        "workflow.gate.release_gate.approval_required",
        "workflow.gate.retro.governance_created",
    } <= event_types
    release_event_payloads = [
        json_loads(row["payload"])
        for row in workflow_events
        if row["type"] == "workflow.gate.release_gate.approval_required"
    ]
    assert release_event_payloads[0]["environment"] == "production"
    assert release_event_payloads[0]["jobId"] == release_jobs[0]["id"]

    audit_actions = {event["action"] for event in store.events.list_audit_events(project["id"])}
    assert {
        "workflow.gate.pr_review.evidence_required",
        "workflow.gate.release_gate.approval_required",
        "workflow.gate.retro.governance_created",
    } <= audit_actions

    governance = client.get("/api/v1/governance").json()
    assert any(
        item["metadata"].get("sourceType") == "workflow_retro" for item in governance["architectureDecisions"]
    )
    assert any(item["metadata"].get("sourceType") == "workflow_retro" for item in governance["risks"])
    assert any(item["metadata"].get("sourceType") == "workflow_retro" for item in governance["nextSteps"])


def test_workflow_creation_blocks_force_push_and_direct_main_edit(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(
        name="Unsafe Release Flow", path=tmp_path / "unsafe-release-flow", template_id="other"
    )

    force_push = client.post(
        "/api/v1/workflows",
        json={
            "projectId": project["id"],
            "kind": "pr_release_retro",
            "title": "Unsafe force push",
            "metadata": {"steps": [{"name": "release_gate", "environment": "production", "forcePush": True}]},
        },
        headers=headers,
    )
    assert force_push.status_code == 422
    assert "force push" in force_push.json()["detail"].lower()

    direct_main = client.post(
        "/api/v1/workflows",
        json={
            "projectId": project["id"],
            "kind": "pr_release_retro",
            "title": "Unsafe direct main edit",
            "metadata": {
                "steps": [
                    {
                        "name": "implementation",
                        "branch": "main",
                        "operation": "direct_push",
                    }
                ]
            },
        },
        headers=headers,
    )
    assert direct_main.status_code == 422
    assert "direct main" in direct_main.json()["detail"].lower()


def test_pr_review_gate_advances_only_with_passed_qa_evidence(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project, started = create_and_start_release_workflow(store, client, headers, tmp_path)
    workflow = started["workflow"]
    run = started["workflowRun"]
    pr_step = next(step for step in started["workflowSteps"] if step["name"] == "pr_review")

    blocked = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps/{pr_step['id']}/advance",
        json={"reason": "try without QA"},
        headers=headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "pr_review requires passed QA evidence for this workflow run."

    qa_log = "\n".join(f"qa line {index}" for index in range(2000))
    evidence = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "workflowRunId": run["id"],
            "taskId": "qa_validation",
            "testPlan": "Regression gate",
            "logs": [{"name": "qa-results.log", "content": qa_log}],
            "qaVerdict": "passed",
            **real_qa_evidence_fields(),
        },
        headers=headers,
    )
    assert evidence.status_code == 201

    advanced = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps/{pr_step['id']}/advance",
        json={"reason": "QA passed", "evidencePackageId": evidence.json()["evidencePackage"]["id"]},
        headers=headers,
    )
    assert advanced.status_code == 202
    body = advanced.json()
    assert body["advanced"] is True
    assert body["workflowStep"]["status"] == "completed"
    assert body["workflowStep"]["metadata"]["gateState"] == "evidence_satisfied"
    assert body["workflowStep"]["metadata"]["evidencePackageId"] == evidence.json()["evidencePackage"]["id"]

    event_types = {
        row["type"]
        for row in store.connection.execute(
            "SELECT type FROM workflow_events WHERE step_id = ?",
            (pr_step["id"],),
        ).fetchall()
    }
    assert {"workflow.gate.pr_review.blocked", "workflow.gate.pr_review.advanced"} <= event_types
    audit_actions = {event["action"] for event in store.events.list_audit_events(project["id"])}
    assert {"workflow.gate.pr_review.blocked", "workflow.gate.pr_review.advanced"} <= audit_actions


def test_pr_review_rejects_passed_verdict_with_failed_test_results(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project, started = create_and_start_release_workflow(store, client, headers, tmp_path)
    workflow = started["workflow"]
    run = started["workflowRun"]
    pr_step = next(step for step in started["workflowSteps"] if step["name"] == "pr_review")
    evidence = store.evidence.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=run["id"],
        agent_id="qa",
        task_id="qa_validation",
        test_plan="Contradictory QA",
        test_results=[
            {"command": "uv run pytest", "status": "passed"},
            {"command": "corepack pnpm test:web", "status": "failed"},
        ],
        evidence_source="qa_passed_by_command",
        qa_verdict="passed",
    )

    blocked = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps/{pr_step['id']}/advance",
        json={"reason": "QA package claims passed", "evidencePackageId": evidence["id"]},
        headers=headers,
    )

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "pr_review requires passed QA evidence for this workflow run."


def test_release_gate_waits_for_human_approval_before_advance(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project, started = create_and_start_release_workflow(store, client, headers, tmp_path)
    workflow = started["workflow"]
    release_step = next(step for step in started["workflowSteps"] if step["name"] == "release_gate")
    release_job = next(
        job
        for job in store.jobs.list_jobs_for_workflow_runs([started["workflowRun"]["id"]])
        if job["kind"] == "release.production"
    )
    action = store.jobs.list_action_requests(release_job["id"])[0]

    blocked = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps/{release_step['id']}/advance",
        json={"reason": "release now"},
        headers=headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "release_gate requires approved production release action requests."

    approved = client.post(
        f"/api/v1/jobs/{release_job['id']}/actions/{action['id']}/approve",
        json={"reason": "explicit human release approval"},
        headers=headers,
    )
    assert approved.status_code == 202

    advanced = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps/{release_step['id']}/advance",
        json={"reason": "approved release gate"},
        headers=headers,
    )
    assert advanced.status_code == 202
    assert advanced.json()["workflowStep"]["metadata"]["gateState"] == "approval_satisfied"
    assert advanced.json()["workflowStep"]["status"] == "completed"
    audit_actions = {event["action"] for event in store.events.list_audit_events(project["id"])}
    assert {"workflow.gate.release_gate.blocked", "workflow.gate.release_gate.advanced"} <= audit_actions


def test_retro_gate_advances_when_governance_records_exist(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project, started = create_and_start_release_workflow(store, client, headers, tmp_path)
    workflow = started["workflow"]
    retro_step = next(step for step in started["workflowSteps"] if step["name"] == "retro")

    advanced = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps/{retro_step['id']}/advance",
        json={"reason": "retro records seeded"},
        headers=headers,
    )
    assert advanced.status_code == 202
    assert advanced.json()["workflowStep"]["metadata"]["gateState"] == "retro_recorded"
    assert advanced.json()["workflowStep"]["status"] == "completed"
    audit_actions = {event["action"] for event in store.events.list_audit_events(project["id"])}
    assert "workflow.gate.retro.advanced" in audit_actions
