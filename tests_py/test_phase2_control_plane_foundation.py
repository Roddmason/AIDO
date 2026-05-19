from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_phase2_schema_adds_control_plane_foundation_tables(tmp_path: Path) -> None:
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()

    with sqlite3.connect(tmp_path / "platform.sqlite") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 2 in migrations
    assert {
        "workflows",
        "workflow_runs",
        "workflow_steps",
        "workflow_events",
        "permission_policies",
        "permission_decisions",
        "evidence_packages",
        "agent_profiles",
        "model_policies",
        "model_calls",
        "cost_usage",
    } <= tables


def test_workflows_policy_evidence_agents_and_model_policy_routes_are_real(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Phase 2", path=tmp_path / "phase2", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    workflow = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "kind": "idea_to_pr", "title": "Ship evidence flow"},
        headers=headers,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["workflow"]["id"]
    assert workflow.json()["workflow"]["status"] == "queued"

    started = client.post(f"/api/v1/workflows/{workflow_id}/start", json={"reason": "phase2 test"}, headers=headers)
    assert started.status_code == 202
    assert started.json()["workflowRun"]["status"] == "running"
    assert {step["name"] for step in started.json()["workflowSteps"]} >= {"idea_intake", "project_discovery"}

    paused = client.post(f"/api/v1/workflows/{workflow_id}/pause", json={"reason": "human check"}, headers=headers)
    assert paused.status_code == 202
    assert paused.json()["workflow"]["status"] == "paused"

    policy = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "agentId": "agent-test",
            "role": "implementer",
            "tool": "shell",
            "command": "git push origin main --force",
            "path": str(tmp_path),
            "workspaceId": "workspace-test",
            "gitOperation": "force_push",
        },
        headers=headers,
    )
    assert policy.status_code == 200
    decision = policy.json()["decision"]
    assert decision["decision"] in {"deny", "requires_human"}
    assert decision["riskLevel"] == "critical"
    assert decision["id"].startswith("permission-decision-")

    evidence = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "workflowRunId": started.json()["workflowRun"]["id"],
            "agentId": "qa-reviewer",
            "taskId": "story-evidence",
            "testPlan": "Run API and UI smoke tests.",
            "acceptanceChecklist": ["workflow created", "policy decision recorded"],
            "qaVerdict": "needs_human_review",
        },
        headers=headers,
    )
    assert evidence.status_code == 201
    evidence_body = evidence.json()["evidencePackage"]
    assert evidence_body["qaVerdict"] == "needs_human_review"
    assert "workflow created" in evidence_body["acceptanceChecklist"]

    profile = client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "qa_reviewer",
            "name": "QA Reviewer",
            "role": "qa_reviewer",
            "runtimeType": "internal_mock",
            "allowedTools": ["tests.read"],
            "qualityGates": ["evidence_required"],
        },
        headers=headers,
    )
    assert profile.status_code == 201
    assert profile.json()["agentProfile"]["role"] == "qa_reviewer"

    model_policy = client.post(
        "/api/v1/model-policies",
        json={
            "id": "implementation_default",
            "name": "Implementation Default",
            "preferred": [{"provider": "local_ollama", "model": "dev-model"}],
            "fallback": [],
            "maxCostUsd": 2.0,
            "maxTokens": 120000,
            "temperature": 0.2,
            "allowRemote": False,
            "allowLocal": True,
        },
        headers=headers,
    )
    assert model_policy.status_code == 201
    assert model_policy.json()["modelPolicy"]["allowLocal"] is True

    overview = client.get("/api/v1/overview").json()
    assert any(item["id"] == workflow_id for item in overview["workflows"])
    assert any(item["id"] == decision["id"] for item in overview["permissionDecisions"])
    assert any(item["id"] == evidence_body["id"] for item in overview["evidencePackages"])
    assert any(item["id"] == "qa_reviewer" for item in overview["agentProfiles"])
    assert any(item["id"] == "implementation_default" for item in overview["modelPolicies"])
    assert any(event["type"] == "workflow.started" for event in overview["events"])


def test_phase2_write_routes_require_loopback_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Auth", path=tmp_path / "auth", template_id="other")
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)

    response = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "kind": "idea_to_pr", "title": "Denied"},
    )
    assert response.status_code == 403
