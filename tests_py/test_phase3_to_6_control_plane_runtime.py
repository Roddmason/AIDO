from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.security_policy.command_classifier import classify_command
from local_control_center.store import PlatformStore


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def test_phase3_to_6_schema_adds_workspaces_runtime_skills_and_evidence_tables(tmp_path: Path) -> None:
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

    assert 3 in migrations
    assert {
        "workspaces",
        "workspace_allocations",
        "workspace_files",
        "workspace_sessions",
        "git_branches",
        "pull_requests",
        "skills",
        "skill_versions",
        "skill_bindings",
        "artifacts",
        "test_results",
        "qa_verdicts",
        "model_providers",
    } <= tables


def test_command_classifier_and_policy_engine_gate_sensitive_actions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Policy", path=tmp_path / "policy", template_id="other")
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    destructive = classify_command("Remove-Item C:\\Users\\Rodd -Recurse -Force")
    assert destructive["riskLevel"] == "critical"
    assert "destructive_delete" in destructive["categories"]

    safe_test = classify_command("uv run pytest tests_py -q")
    assert safe_test["riskLevel"] == "low"
    assert "test" in safe_test["categories"]

    decision = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "role": "implementer",
            "tool": "shell",
            "command": "uv run pytest tests_py -q",
            "path": str(tmp_path / "policy"),
            "workspaceId": "workspace-safe",
        },
        headers=headers,
    )
    assert decision.status_code == 200
    assert decision.json()["decision"]["decision"] == "allow"

    denied = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "role": "implementer",
            "tool": "shell",
            "command": "Remove-Item C:\\Users\\Rodd -Recurse -Force",
            "path": "C:\\Users\\Rodd",
            "workspaceId": "workspace-unsafe",
        },
        headers=headers,
    )
    assert denied.status_code == 200
    assert denied.json()["decision"]["decision"] == "requires_human"
    assert denied.json()["decision"]["riskLevel"] == "critical"


def test_workspace_allocation_enforces_single_owner_and_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Workspace", path=tmp_path / "project", template_id="other")
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    created = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-1", "agentId": "implementer"},
        headers=headers,
    )
    assert created.status_code == 201
    workspace = created.json()["workspace"]
    assert workspace["status"] == "ready"
    assert workspace["ownerAgentId"] == "implementer"
    assert Path(workspace["path"]).exists()

    duplicate = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-1", "agentId": "qa_reviewer"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    archived = client.post(f"/api/v1/workspaces/{workspace['id']}/archive", json={"reason": "done"}, headers=headers)
    assert archived.status_code == 202
    assert archived.json()["workspace"]["status"] == "archived"


def test_internal_mock_agent_run_records_tool_model_cost_and_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Agent", path=tmp_path / "agent", template_id="other")
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "implementer",
            "name": "Implementer",
            "role": "implementer",
            "runtimeType": "internal_mock",
            "modelPolicyId": "implementation_default",
            "allowedTools": ["policy.evaluate", "evidence.create"],
            "qualityGates": ["structured_output"],
        },
        headers=headers,
    )
    client.post(
        "/api/v1/model-policies",
        json={
            "id": "implementation_default",
            "name": "Implementation Default",
            "preferred": [{"provider": "internal_mock", "model": "mock"}],
            "fallback": [],
            "maxCostUsd": 1.0,
            "maxTokens": 4000,
            "temperature": 0.2,
            "allowRemote": False,
            "allowLocal": True,
        },
        headers=headers,
    )

    run = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "implementer",
            "taskId": "story-2",
            "input": {"goal": "produce a structured patch plan"},
        },
        headers=headers,
    )
    assert run.status_code == 202
    agent_run = run.json()["agentRun"]
    assert agent_run["status"] == "completed"
    assert agent_run["output"]["verdict"] == "approved_with_risks"
    assert agent_run["output"]["task_id"] == "story-2"

    overview = client.get("/api/v1/overview").json()
    assert any(call["agentRunId"] == agent_run["id"] for call in overview["agentToolCalls"])
    assert any(call["agentRunId"] == agent_run["id"] for call in overview["modelCalls"])
    assert any(item["scope"] == "model_call" for item in overview["costUsage"])


def test_skills_sync_reads_versionable_local_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    skill_dir = tmp_path / "skills" / "backend-api-contract"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "name: backend-api-contract",
                "description: Validate backend API contracts.",
                "license: Proprietary",
                "compatibility: AIDO",
                "inputs: [openapi]",
                "outputs: [contract-report]",
                "tools: [pytest]",
                "risk_level: low",
                "instructions: Run API contract checks.",
            ]
        ),
        encoding="utf-8",
    )
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    synced = client.post("/api/v1/skills/sync", json={"skillsPath": str(tmp_path / "skills")}, headers=headers)
    assert synced.status_code == 202
    assert synced.json()["synced"] == 1

    listed = client.get("/api/v1/skills")
    assert listed.status_code == 200
    assert listed.json()["skills"][0]["name"] == "backend-api-contract"


def test_qa_cannot_pass_without_test_results_or_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = PlatformStore(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="QA", path=tmp_path / "qa", template_id="other")
    app = create_app(store=store, static_dir=None)
    client = TestClient(app)
    headers = auth_headers(client)

    rejected = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-no-evidence",
            "testPlan": "Run tests",
            "qaVerdict": "passed",
        },
        headers=headers,
    )
    assert rejected.status_code == 422

    accepted = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-with-evidence",
            "testPlan": "Run tests",
            "qaVerdict": "passed",
            "testResults": [{"command": "uv run pytest tests_py -q", "status": "passed"}],
        },
        headers=headers,
    )
    assert accepted.status_code == 201
    assert accepted.json()["evidencePackage"]["qaVerdict"] == "passed"
