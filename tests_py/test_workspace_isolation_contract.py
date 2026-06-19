from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def create_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    assert run_git(["init"], cwd=path).returncode == 0
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=path).returncode == 0
    commit = run_git(
        [
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            "initial",
        ],
        cwd=path,
    )
    assert commit.returncode == 0, commit.stderr
    head = run_git(["rev-parse", "HEAD"], cwd=path)
    assert head.returncode == 0
    return head.stdout.strip()


def test_non_git_workspace_is_isolated_copy_with_manifest_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project_root = tmp_path / "plain-project"
    (project_root / "src").mkdir(parents=True)
    (project_root / "src" / "app.py").write_text("print('root')\n", encoding="utf-8")
    project = store.create_project(name="Plain", path=project_root, template_id="other")

    response = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-copy", "agentId": "implementer"},
        headers=headers,
    )

    assert response.status_code == 201
    workspace = response.json()["workspace"]
    workspace_path = Path(workspace["path"])
    assert workspace["isolationType"] == "directory"
    assert workspace_path != project_root
    assert (workspace_path / "src" / "app.py").read_text(encoding="utf-8") == "print('root')\n"

    (workspace_path / "src" / "app.py").write_text("print('workspace')\n", encoding="utf-8")
    assert (project_root / "src" / "app.py").read_text(encoding="utf-8") == "print('root')\n"

    manifest = workspace["metadata"]["workspaceManifest"]
    assert manifest["workspaceId"] == workspace["id"]
    assert manifest["sourcePath"] == str(project_root)
    assert manifest["workspacePath"] == str(workspace_path)
    assert manifest["ownerAgentId"] == "implementer"
    assert manifest["taskId"] == "story-copy"
    assert manifest["sourceCommit"] is None
    assert manifest["branch"] is None
    assert manifest["limits"]["maxFiles"] > 0
    assert Path(manifest["manifestPath"]).exists()
    files = {item["path"]: item for item in manifest["fileManifest"]["files"]}
    assert files["src/app.py"]["sha256"]


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_git_project_allocation_uses_real_worktree_and_preserves_main_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    source_commit = create_git_repo(repo)
    project = store.create_project(name="Git Project", path=repo, template_id="other")

    response = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-git", "agentId": "implementer"},
        headers=headers,
    )

    assert response.status_code == 201
    workspace = response.json()["workspace"]
    workspace_path = Path(workspace["path"])
    assert workspace["isolationType"] == "git_worktree"
    assert workspace_path != repo
    assert (workspace_path / ".git").exists()
    assert workspace["metadata"]["workspaceManifest"]["sourceCommit"] == source_commit
    assert workspace["metadata"]["workspaceManifest"]["branch"].startswith("aido/story-git/")

    (workspace_path / "README.md").write_text("changed in workspace\n", encoding="utf-8")
    assert (repo / "README.md").read_text(encoding="utf-8") == "initial\n"


def test_agent_tool_execution_without_workspace_is_blocked_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(
        name="No Workspace Run", path=tmp_path / "no-workspace", template_id="other"
    )
    client.post(
        "/api/v1/agent-profiles",
        json={
            "id": "isolated-agent",
            "name": "Isolated Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        },
        headers=headers,
    )

    def fail_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("runtime must not execute without an allocated workspace")

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute", fail_execute
    )

    response = client.post(
        "/api/v1/agent-runs",
        json={
            "projectId": project["id"],
            "agentProfileId": "isolated-agent",
            "taskId": "story-no-workspace",
            "input": {
                "toolCalls": [
                    {
                        "tool": "shell",
                        "command": "python --version",
                        "argv": ["python", "--version"],
                        "path": project["path"],
                        "execute": True,
                    }
                ]
            },
        },
        headers=headers,
    )

    assert response.status_code == 202
    agent_run = response.json()["agentRun"]
    assert agent_run["status"] == "failed"
    assert agent_run["output"]["verdict"] == "blocked"
    tool_call = agent_run["output"]["tool_calls"][0]
    assert tool_call["status"] == "denied"
    assert "workspace" in tool_call["payload"]["decisionReason"].lower()


def test_runtime_path_outside_registered_workspace_is_denied_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project = store.create_project(
        name="Workspace Boundary", path=tmp_path / "boundary-project", template_id="other"
    )
    workspace = WorkspacesRepository(store.connection, root=tmp_path).allocate_workspace(
        project_id=project["id"],
        task_id="story-boundary",
        agent_id="implementer",
    )
    agents = AgentsRepository(store.connection)
    profile = agents.upsert_agent_profile(
        {
            "id": "boundary-agent",
            "name": "Boundary Agent",
            "role": "implementer",
            "runtimeMode": "cli",
            "permissionProfile": "dev_safe",
            "allowedTools": ["shell"],
        }
    )
    run = agents.create_agent_run(
        project_id=project["id"],
        agent_profile_id=profile["id"],
        task_id="story-boundary",
        input_payload={},
        output_payload={},
        status="running",
    )

    def fail_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("runtime must not execute outside the registered workspace")

    monkeypatch.setattr(
        "local_control_center.security_policy.sandbox.RestrictedSubprocessSandbox.execute", fail_execute
    )

    result = ToolBroker(store.connection).evaluate_tool_call(
        project_id=project["id"],
        agent_run_id=run["id"],
        agent_profile=profile,
        tool_call={
            "tool": "shell",
            "command": "python --version",
            "argv": ["python", "--version"],
            "workspaceId": workspace["id"],
            "workspacePath": workspace["path"],
            "path": str(tmp_path),
            "execute": True,
        },
    )

    assert result["decision"]["decision"] == "deny"
    assert result["toolCall"]["status"] == "denied"
    assert "outside the allocated workspace" in result["decision"]["reason"]


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_workspace_archive_keeps_evidence_after_worktree_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "cleanup-repo"
    create_git_repo(repo)
    project = store.create_project(name="Cleanup Evidence", path=repo, template_id="other")
    workspace = client.post(
        "/api/v1/workspaces",
        json={"projectId": project["id"], "taskId": "story-cleanup", "agentId": "implementer"},
        headers=headers,
    ).json()["workspace"]
    workspace_path = Path(workspace["path"])
    (workspace_path / "README.md").write_text("changed\n", encoding="utf-8")

    archived = client.post(
        f"/api/v1/workspaces/{workspace['id']}/archive",
        json={"reason": "capture before cleanup"},
        headers=headers,
    )

    assert archived.status_code == 202
    assert not workspace_path.exists()
    evidence = archived.json()["evidencePackage"]
    kinds = {ref["kind"] for ref in evidence["diffRefs"]}
    assert {"git_diff", "workspace_manifest", "workspace_snapshot"} <= kinds
    git_diff = next(ref for ref in evidence["diffRefs"] if ref["kind"] == "git_diff")
    assert git_diff["state"] == "captured"
    assert "README.md" in git_diff["nameOnly"]
    snapshot = next(ref for ref in evidence["diffRefs"] if ref["kind"] == "workspace_snapshot")
    assert any(item["path"] == "README.md" and item["sha256"] for item in snapshot["files"])
    persisted = store.evidence.get_evidence_package(evidence["id"])
    assert persisted["id"] == evidence["id"]
    assert any(ref["kind"] == "workspace_snapshot" for ref in persisted["diffRefs"])
