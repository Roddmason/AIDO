from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
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


def test_policy_allows_low_risk_shell_only_inside_allocated_workspace(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Policy Workspace", path=tmp_path / "project", template_id="other")
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-policy", "agentId": "implementer"},
        headers=headers,
    )
    workspace = workspace_response.json()["workspace"]
    workspace_path = Path(workspace["path"])

    allowed = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "agentId": "implementer",
            "role": "implementer",
            "tool": "shell",
            "command": "uv run pytest tests_py -q",
            "path": str(workspace_path / "tests_py"),
        },
        headers=headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["decision"]["decision"] == "allow"

    outside = client.post(
        "/api/v1/policies/evaluate",
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "agentId": "implementer",
            "role": "implementer",
            "tool": "shell",
            "command": "uv run pytest tests_py -q",
            "path": str(tmp_path.parent),
        },
        headers=headers,
    )
    assert outside.status_code == 200
    decision = outside.json()["decision"]
    assert decision["decision"] == "requires_approval"
    assert decision["riskLevel"] == "medium"
    assert "path_outside_workspace" in decision["payload"]["categories"]


def test_git_worktree_request_degrades_safely_when_project_is_not_repo(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="No Git", path=tmp_path / "plain-project", template_id="other")

    created = client.post(
        "/api/v1/workspaces",
        json={
            "projectId": project["id"],
            "taskId": "story-no-git",
            "agentId": "implementer",
            "isolationType": "git_worktree",
        },
        headers=headers,
    )
    assert created.status_code == 201
    workspace = created.json()["workspace"]
    assert workspace["isolationType"] == "directory"
    assert workspace["metadata"]["gitWorktree"]["status"] == "degraded_not_git_repo"
    assert Path(workspace["path"]).exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git CLI is not available")
def test_git_worktree_request_creates_branch_and_records_metadata(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=AIDO", "-c", "user.email=aido@example.invalid", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    project = store.create_project(name="Git", path=repo, template_id="other")

    created = client.post(
        "/api/v1/workspaces",
        json={
            "projectId": project["id"],
            "taskId": "story-git",
            "agentId": "implementer",
            "isolationType": "git_worktree",
            "baseBranch": "HEAD",
        },
        headers=headers,
    )
    assert created.status_code == 201
    workspace = created.json()["workspace"]
    assert workspace["isolationType"] == "git_worktree"
    assert workspace["metadata"]["gitWorktree"]["status"] == "created"
    assert workspace["metadata"]["gitWorktree"]["branchName"].startswith("aido/story-git/")

    with sqlite3.connect(tmp_path / "platform.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        branch = connection.execute(
            "SELECT * FROM git_branches WHERE workspace_id = ?",
            (workspace["id"],),
        ).fetchone()
    assert branch is not None
    assert branch["status"] == "active"


def test_workflow_workspace_evidence_traceability_is_exposed(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Trace", path=tmp_path / "trace", template_id="other")
    workflow = client.post(
        "/api/v1/workflows",
        json={"projectId": project["id"], "title": "Trace story"},
        headers=headers,
    ).json()["workflow"]
    run = client.post(f"/api/v1/workflows/{workflow['id']}/start", json={"reason": "trace"}, headers=headers).json()
    workflow_run = run["workflowRun"]
    workspace_step = next(step for step in run["workflowSteps"] if step["name"] == "workspace_create")

    workspace = client.post(
        "/api/v1/workspaces",
        json={
            "projectId": project["id"],
            "taskId": "story-trace",
            "agentId": "implementer",
            "workflowRunId": workflow_run["id"],
            "workflowStepId": workspace_step["id"],
        },
        headers=headers,
    ).json()["workspace"]

    evidence = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "workflowRunId": workflow_run["id"],
            "agentId": "implementer",
            "taskId": "story-trace",
            "testPlan": "Run local tests",
            "qaVerdict": "passed",
            "testResults": [{"command": "uv run pytest tests_py -q", "status": "passed"}],
            "workspaceId": workspace["id"],
        },
        headers=headers,
    ).json()["evidencePackage"]

    detail = client.get(f"/api/v1/workflows/{workflow['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert any(item["id"] == workspace["id"] for item in body["workspaces"])
    assert any(item["id"] == evidence["id"] for item in body["evidencePackages"])


def test_evidence_detail_exposes_persisted_test_result_records(tmp_path: Path, monkeypatch) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(name="Evidence Detail", path=tmp_path / "evidence", template_id="other")
    evidence = client.post(
        "/api/v1/evidence",
        json={
            "projectId": project["id"],
            "taskId": "story-evidence-detail",
            "testPlan": "Run focused tests",
            "qaVerdict": "passed",
            "testResults": [
                {
                    "command": "uv run pytest tests_py/test_phase7_workspace_policy_traceability.py -q",
                    "status": "passed",
                    "durationMs": 1200,
                    "outputRef": "artifact://pytest-log",
                }
            ],
        },
        headers=headers,
    ).json()["evidencePackage"]

    detail = client.get(f"/api/v1/evidence/{evidence['id']}")
    assert detail.status_code == 200
    records = detail.json()["testResultRecords"]
    assert len(records) == 1
    assert records[0]["status"] == "passed"
    assert records[0]["durationMs"] == 1200
