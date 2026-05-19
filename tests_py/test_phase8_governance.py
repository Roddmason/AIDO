from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.store import PlatformStore


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_app(tmp_path: Path, monkeypatch) -> tuple[PlatformStore, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(store=store, static_dir=None))
    return store, client, auth_headers(client)


def test_governance_schema_adds_architecture_risks_and_next_steps(tmp_path: Path) -> None:
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()

    with sqlite3.connect(tmp_path / "platform.sqlite") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert 5 in migrations
    assert {"architecture_decisions", "risk_register", "next_steps"} <= tables


def test_governance_api_persists_decisions_risks_and_next_steps(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Governance", path=tmp_path / "governance", template_id="other")

    rejected_risk = client.post(
        "/api/v1/risks",
        json={
            "projectId": project["id"],
            "title": "Unbounded shell execution",
            "severity": "high",
            "status": "open",
            "description": "Shell command scopes can drift.",
        },
        headers=headers,
    )
    assert rejected_risk.status_code == 422

    risk = client.post(
        "/api/v1/risks",
        json={
            "projectId": project["id"],
            "title": "Unbounded shell execution",
            "severity": "high",
            "status": "open",
            "description": "Shell command scopes can drift.",
            "mitigation": "Keep path-aware policy and sandbox gate before execution.",
            "owner": "tech_lead",
        },
        headers=headers,
    )
    assert risk.status_code == 201
    risk_body = risk.json()["risk"]
    assert risk_body["severity"] == "high"

    next_step = client.post(
        "/api/v1/next-steps",
        json={
            "projectId": project["id"],
            "title": "Capture workspace diff before archive",
            "status": "planned",
            "priority": "high",
            "sourceRiskId": risk_body["id"],
        },
        headers=headers,
    )
    assert next_step.status_code == 201
    next_step_body = next_step.json()["nextStep"]

    decision = client.post(
        "/api/v1/architecture-decisions",
        json={
            "projectId": project["id"],
            "title": "Keep Task Scheduler as default Windows supervisor",
            "status": "accepted",
            "context": "The local backend depends on user-scoped CLI auth and profile PATH.",
            "decision": "Use user-scoped Task Scheduler by default; keep Windows Service optional.",
            "consequences": [
                "No admin account required for MVP",
                "Service-mode hardening remains a later optional path",
            ],
            "linkedRiskIds": [risk_body["id"]],
            "nextStepIds": [next_step_body["id"]],
        },
        headers=headers,
    )
    assert decision.status_code == 201
    decision_body = decision.json()["architectureDecision"]
    assert decision_body["linkedRiskIds"] == [risk_body["id"]]
    assert decision_body["nextStepIds"] == [next_step_body["id"]]

    governance = client.get("/api/v1/governance")
    assert governance.status_code == 200
    assert any(item["id"] == decision_body["id"] for item in governance.json()["architectureDecisions"])
    assert any(item["id"] == risk_body["id"] for item in governance.json()["risks"])
    assert any(item["id"] == next_step_body["id"] for item in governance.json()["nextSteps"])

    overview = client.get("/api/v1/overview").json()
    assert any(item["id"] == decision_body["id"] for item in overview["architectureDecisions"])
    assert any(item["id"] == risk_body["id"] for item in overview["riskRegister"])
    assert any(item["id"] == next_step_body["id"] for item in overview["nextSteps"])


def test_risk_and_next_step_status_updates_are_audited(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Governance Updates", path=tmp_path / "governance-updates", template_id="other")
    risk = client.post(
        "/api/v1/risks",
        json={
            "projectId": project["id"],
            "title": "No diff evidence",
            "severity": "medium",
            "status": "open",
            "description": "Archive can happen before diff capture.",
            "mitigation": "Capture dirty state before archive.",
        },
        headers=headers,
    ).json()["risk"]
    next_step = client.post(
        "/api/v1/next-steps",
        json={
            "projectId": project["id"],
            "title": "Add diff artifact capture",
            "status": "planned",
            "priority": "high",
            "sourceRiskId": risk["id"],
        },
        headers=headers,
    ).json()["nextStep"]

    patched_risk = client.patch(
        f"/api/v1/risks/{risk['id']}",
        json={"status": "mitigating", "mitigation": "Implementation started."},
        headers=headers,
    )
    assert patched_risk.status_code == 202
    assert patched_risk.json()["risk"]["status"] == "mitigating"

    patched_step = client.patch(
        f"/api/v1/next-steps/{next_step['id']}",
        json={"status": "in_progress"},
        headers=headers,
    )
    assert patched_step.status_code == 202
    assert patched_step.json()["nextStep"]["status"] == "in_progress"

    audit_actions = [event["action"] for event in client.get("/api/v1/overview").json()["auditEvents"]]
    assert "risk.update" in audit_actions
    assert "next_step.update" in audit_actions


def test_governance_enums_reject_invalid_statuses_severities_and_priorities(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Governance Enums", path=tmp_path / "governance-enums", template_id="other")

    invalid_risk = client.post(
        "/api/v1/risks",
        json={
            "projectId": project["id"],
            "title": "Invalid severity",
            "severity": "severe-ish",
            "mitigation": "Should be rejected before persistence.",
        },
        headers=headers,
    )
    assert invalid_risk.status_code == 422

    invalid_step = client.post(
        "/api/v1/next-steps",
        json={
            "projectId": project["id"],
            "title": "Invalid priority",
            "priority": "whenever",
        },
        headers=headers,
    )
    assert invalid_step.status_code == 422

    invalid_decision = client.post(
        "/api/v1/architecture-decisions",
        json={
            "projectId": project["id"],
            "title": "Invalid ADR status",
            "status": "maybe",
        },
        headers=headers,
    )
    assert invalid_decision.status_code == 422


def test_policy_qa_and_workflow_failures_create_governance_risks(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Governance Signals", path=tmp_path / "governance-signals", template_id="other")

    policy = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "agentId": "security_reviewer",
            "role": "security_reviewer",
            "tool": "shell",
            "command": "rm -rf .",
            "path": str(tmp_path / "governance-signals"),
        },
        headers=headers,
    )
    assert policy.status_code == 200
    assert policy.json()["decision"]["decision"] == "requires_human"

    evidence = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "agentId": "qa_reviewer",
            "taskId": "story-governance",
            "testPlan": "Run failed QA smoke",
            "qaVerdict": "failed",
            "riskNotes": ["Smoke failed on Windows shutdown."],
        },
        headers=headers,
    )
    assert evidence.status_code == 201

    workflow = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "title": "Risk-linked workflow"},
        headers=headers,
    ).json()["workflow"]
    cancelled = client.post(
        f"/api/v1/workflows/{workflow['id']}/cancel",
        json={"reason": "blocked by missing evidence"},
        headers=headers,
    )
    assert cancelled.status_code == 202

    risks = client.get("/api/v1/governance").json()["risks"]
    sources = {risk["metadata"].get("sourceType") for risk in risks}
    assert {"policy_decision", "qa_verdict", "workflow_status"} <= sources
