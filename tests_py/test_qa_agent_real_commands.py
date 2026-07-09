from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.security_policy.git_command_runner import git_available, run_git
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    app = create_app(runtime=store, static_dir=None)
    client = TestClient(app)
    return store, client, auth_headers(client)


def create_git_project(
    store: ControlPlaneFixture, tmp_path: Path, name: str = "QA Agent Project"
) -> dict[str, Any]:
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# QA agent project\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=project_path).returncode == 0
    commit = run_git(
        [
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            "init",
        ],
        cwd=project_path,
    )
    assert commit.returncode == 0
    return store.create_project(name=name, path=project_path, template_id="other")


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_qa_agent_command_ok_records_passed_verdict_and_artifact_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="QA Agent Pass")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-agent-pass",
        agent_id="qa_agent",
        reason="qa agent pass workspace",
        isolation_type="git_worktree",
    )

    response = client.post(
        "/api/v1/agents/qa/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "qa-agent-pass",
            "commands": [
                {"label": "python version", "argv": [sys.executable, "--version"], "critical": True}
            ],
        },
    )

    assert response.status_code == 202
    body = response.json()
    result = body["results"][0]
    assert body["status"] == "passed"
    assert body["verdict"] == "passed"
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert result["status"] == "passed"
    assert result["exitCode"] == 0
    assert result["durationMs"] is not None
    assert "stdout" in result
    assert "stderr" in result
    assert result["artifactHashes"]["stdoutHash"]
    assert result["artifactHashes"]["stderrHash"]
    assert body["evidencePackage"]["artifactIds"]
    assert body["agentRun"]["status"] == "completed"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_qa_agent_discovers_package_script_with_default_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="QA Agent Discovered Package")
    project_path = Path(project["path"])
    (project_path / "package.json").write_text(
        '{"private":true,"scripts":{"test":"node -e \\"process.exit(0)\\""}}\n',
        encoding="utf-8",
    )
    assert run_git(["add", "package.json"], cwd=project_path).returncode == 0
    commit = run_git(
        [
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            "add qa script",
        ],
        cwd=project_path,
    )
    assert commit.returncode == 0
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-agent-discovered-package",
        agent_id="qa_agent",
        reason="qa agent discovered package workspace",
        isolation_type="git_worktree",
    )

    response = client.post(
        "/api/v1/agents/qa/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "qa-agent-discovered-package",
            "commands": [],
        },
    )

    assert response.status_code == 202
    body = response.json()
    result = body["results"][0]
    assert body["status"] == "passed"
    assert result["command"] == "corepack pnpm@10.24.0 run test"
    assert result["status"] == "passed"
    assert result["exitCode"] == 0


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_qa_agent_rejects_command_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="QA Agent Command String")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-agent-command-string",
        agent_id="qa_agent",
        reason="qa agent command string workspace",
        isolation_type="git_worktree",
    )

    response = client.post(
        "/api/v1/agents/qa/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "qa-agent-command-string",
            "commands": [{"command": "python --version", "critical": True}],
        },
    )

    assert response.status_code == 422
    assert "command" in str(response.json()["detail"]).lower()


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_qa_agent_command_failure_records_failed_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="QA Agent Fail")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-agent-fail",
        agent_id="qa_agent",
        reason="qa agent fail workspace",
        isolation_type="git_worktree",
    )

    response = client.post(
        "/api/v1/agents/qa/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "qa-agent-fail",
            "commands": [
                {
                    "label": "failing pytest",
                    "argv": [sys.executable, "-m", "pytest", "missing_qa_agent_test.py"],
                }
            ],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["verdict"] == "failed"
    assert body["results"][0]["status"] == "failed"
    assert body["results"][0]["exitCode"] != 0
    assert body["evidencePackage"]["qaVerdict"] == "failed"
    assert body["agentRun"]["status"] == "failed"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_qa_agent_missing_noncritical_command_is_skipped_with_reason_not_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="QA Agent Skip")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-agent-skip",
        agent_id="qa_agent",
        reason="qa agent skip workspace",
        isolation_type="git_worktree",
    )

    response = client.post(
        "/api/v1/agents/qa/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "qa-agent-skip",
            "commands": [
                {
                    "label": "optional unknown command",
                    "argv": ["definitely-not-aido-command", "--version"],
                    "critical": False,
                }
            ],
        },
    )

    assert response.status_code == 202
    body = response.json()
    result = body["results"][0]
    assert body["status"] == "skipped_with_reason"
    assert body["verdict"] == "skipped_with_reason"
    assert result["status"] == "skipped_with_reason"
    assert result["exitCode"] is None
    assert result["reason"]
    assert body["evidencePackage"]["qaVerdict"] == "skipped_with_reason"
    assert body["agentRun"]["status"] == "failed"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_qa_agent_missing_critical_command_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path, name="QA Agent Missing Critical")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="qa-agent-critical-missing",
        agent_id="qa_agent",
        reason="qa agent critical missing workspace",
        isolation_type="git_worktree",
    )

    response = client.post(
        "/api/v1/agents/qa/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "qa-agent-critical-missing",
            "commands": [
                {
                    "label": "critical unknown command",
                    "argv": ["definitely-not-aido-command", "--version"],
                    "critical": True,
                }
            ],
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["verdict"] == "failed"
    assert body["results"][0]["status"] == "failed"
    assert body["results"][0]["exitCode"] is None
    assert body["evidencePackage"]["qaVerdict"] == "failed"


def test_qa_verdict_required_for_completed() -> None:
    from local_control_center.agents.qa_agent import qa_verdict_allows_completion

    real_passed = {
        "status": "passed",
        "exitCode": 0,
        "execution": "restricted_subprocess",
        "toolCallId": "agent-tool-call-1",
        "artifactHashes": {
            "stdoutHash": "stdout-hash",
            "stderrHash": "stderr-hash",
            "outputArtifactHash": "output-hash",
        },
    }
    assert qa_verdict_allows_completion("passed", [real_passed]) is True
    assert qa_verdict_allows_completion("passed", [{"status": "passed"}]) is False
    assert qa_verdict_allows_completion("skipped_with_reason", [{"status": "skipped_with_reason"}]) is False
    assert qa_verdict_allows_completion("failed", [{"status": "failed"}]) is False
    assert qa_verdict_allows_completion("blocked", []) is False
