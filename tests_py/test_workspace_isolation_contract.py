from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.tool_broker import ToolBroker
from local_control_center.app import create_app
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.workspaces_projects.repository import (
    WorkspaceConflictError,
    WorkspacesRepository,
)
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
    worktree_metadata = workspace["metadata"]["gitWorktree"]
    assert worktree_metadata["toolCalls"]
    assert worktree_metadata["policyDecisionIds"]
    assert worktree_metadata["toolCalls"][0]["execution"] == "restricted_subprocess"
    assert worktree_metadata["toolCalls"][0]["toolCallStatus"] == "completed"

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


def test_prompt_workspace_archive_refuses_to_delete_outside_controlled_temp_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controlled_temp = tmp_path / "system-temp"
    monkeypatch.setenv("TEMP", str(controlled_temp))
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project_root = tmp_path / "prompt-project"
    project_root.mkdir()
    project = store.create_project(name="Prompt Boundary", path=project_root, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)
    source_workspace = repository.allocate_workspace(
        project_id=project["id"],
        task_id="prompt-source",
        agent_id="product_owner_agent",
    )
    prompt_workspace = repository.allocate_prompt_workspace(
        project_id=project["id"],
        task_id="prompt-runtime",
        agent_id="product_owner_agent",
        source_workspace_id=source_workspace["id"],
        reason="prompt cleanup boundary test",
    )
    outside = tmp_path / "must-survive"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("do not delete\n", encoding="utf-8")
    store.connection.execute(
        "UPDATE workspaces SET path = ? WHERE id = ?",
        (str(outside.resolve()), prompt_workspace["id"]),
    )

    archived = repository.archive_workspace(prompt_workspace["id"], reason="boundary probe")

    cleanup = archived["metadata"]["ephemeralPromptWorkspaceCleanup"]
    assert archived["status"] == "archived"
    assert cleanup["status"] == "refused"
    assert "outside the controlled temp root" in cleanup["reason"]
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"
    allocation = store.connection.execute(
        "SELECT status, released_at FROM workspace_allocations WHERE workspace_id = ?",
        (prompt_workspace["id"],),
    ).fetchone()
    assert allocation is not None
    assert allocation["status"] == "released"
    assert allocation["released_at"]


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
    assert git_diff["toolCalls"]
    assert git_diff["policyDecisionIds"]
    snapshot = next(ref for ref in evidence["diffRefs"] if ref["kind"] == "workspace_snapshot")
    assert any(item["path"] == "README.md" and item["sha256"] for item in snapshot["files"])
    cleanup = archived.json()["workspace"]["metadata"]["gitWorktreeCleanup"]
    assert cleanup["status"] == "removed"
    assert cleanup["toolCalls"]
    assert cleanup["policyDecisionIds"]
    persisted = store.evidence.get_evidence_package(evidence["id"])
    assert persisted["id"] == evidence["id"]
    assert any(ref["kind"] == "workspace_snapshot" for ref in persisted["diffRefs"])


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_reuse_existing_returns_the_same_workspace_instead_of_conflicting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El loop reutiliza un workspace estable por hilo en vez de crear uno nuevo por turno.

    Sin ``reuse_existing`` el guard de conflicto sigue fail-closed (contrato de issue_to_patch);
    con ``reuse_existing`` una segunda asignacion de la misma tarea devuelve el mismo workspace y
    la misma rama, evitando la proliferacion de ramas ``codex/product-*-<hash>`` por turno.
    """
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "reuse-repo"
    create_git_repo(repo)
    project = store.create_project(name="Reuse", path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)

    first = repository.allocate_workspace(
        project_id=project["id"],
        task_id="product-loop-stable123",
        agent_id="developer",
        branch_name="codex/product-loop-stable123",
    )
    store.connection.commit()

    with pytest.raises(WorkspaceConflictError):
        repository.allocate_workspace(
            project_id=project["id"],
            task_id="product-loop-stable123",
            agent_id="developer",
            branch_name="codex/product-loop-stable123",
        )

    reused = repository.allocate_workspace(
        project_id=project["id"],
        task_id="product-loop-stable123",
        agent_id="developer",
        branch_name="codex/product-loop-stable123",
        reuse_existing=True,
    )

    assert reused["id"] == first["id"]
    assert reused["metadata"]["gitWorktree"]["branchName"] == first["metadata"]["gitWorktree"]["branchName"]


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_archive_deletes_the_work_branch_ref_not_just_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Al archivar con ``delete_branch`` se borra el ref de la rama, no solo el worktree.

    Sin esto las ramas ``codex/product-*`` quedaban para siempre en el repo del proyecto.
    """
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "gc-repo"
    create_git_repo(repo)
    project = store.create_project(name="GC", path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)

    workspace = repository.allocate_workspace(
        project_id=project["id"],
        task_id="product-loop-gc123",
        agent_id="developer",
        branch_name="codex/product-loop-gc123",
    )
    store.connection.commit()
    assert run_git(["rev-parse", "--verify", "codex/product-loop-gc123"], cwd=repo).returncode == 0

    repository.archive_workspace(workspace["id"], reason="done", delete_branch=True)

    assert run_git(["rev-parse", "--verify", "codex/product-loop-gc123"], cwd=repo).returncode != 0


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_worktree_forks_from_configured_base_branch_and_falls_back_to_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El worktree se abre sobre la rama base configurada; si no existe, cae a HEAD sin degradar.

    Habilita ``project.git.baseBranch``: el loop puede forkear de ``dev`` en vez del HEAD actual, y
    un proyecto que no tenga esa rama nunca pierde el aislamiento git (cae a HEAD).
    """
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "base-repo"
    create_git_repo(repo)
    assert run_git(["branch", "integration"], cwd=repo).returncode == 0
    project = store.create_project(name="Base", path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)

    on_integration = repository.allocate_workspace(
        project_id=project["id"],
        task_id="base-exists",
        agent_id="developer",
        branch_name="codex/base-exists",
        base_branch="integration",
    )
    store.connection.commit()
    assert on_integration["isolationType"] == "git_worktree"
    assert on_integration["metadata"]["gitWorktree"]["baseBranch"] == "integration"

    fell_back = repository.allocate_workspace(
        project_id=project["id"],
        task_id="base-missing",
        agent_id="developer",
        branch_name="codex/base-missing",
        base_branch="does-not-exist",
    )
    assert fell_back["isolationType"] == "git_worktree"
    assert fell_back["metadata"]["gitWorktree"]["baseBranch"] == "HEAD"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_commit_workspace_changes_persists_agent_work_on_the_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El trabajo del agente pasa de patch efímero a commit real sobre la rama de la HU."""
    from local_control_center.workspaces_projects.git_worktrees import commit_workspace_changes

    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "commit-repo"
    create_git_repo(repo)
    project = store.create_project(name="Commit", path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)
    workspace = repository.allocate_workspace(
        project_id=project["id"],
        task_id="commit-task",
        agent_id="developer",
        branch_name="codex/commit-task",
    )
    store.connection.commit()
    workspace_path = Path(workspace["path"])
    (workspace_path / "feature.py").write_text("x = 1\n", encoding="utf-8")

    result = commit_workspace_changes(
        workspace_path=workspace_path,
        message="Feature: add feature.py",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )
    store.connection.commit()

    assert result["status"] == "committed", result
    assert result["commit"]
    show = run_git(["-C", str(workspace_path), "show", "--name-only", "HEAD"])
    assert "feature.py" in show.stdout

    again = commit_workspace_changes(
        workspace_path=workspace_path,
        message="noop",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )
    assert again["status"] == "nothing_to_commit"


def _committed_work_branch(
    store: Any, tmp_path: Path, repo: Path, project: dict[str, Any], *, task: str
) -> tuple[dict[str, Any], str]:
    """Asigna un worktree, escribe y commitea un archivo; devuelve (workspace, rama de trabajo)."""
    from local_control_center.workspaces_projects.git_worktrees import commit_workspace_changes

    repository = WorkspacesRepository(store.connection, root=tmp_path)
    workspace = repository.allocate_workspace(
        project_id=project["id"],
        task_id=task,
        agent_id="developer",
        branch_name=f"codex/{task}",
    )
    store.connection.commit()
    workspace_path = Path(workspace["path"])
    (workspace_path / f"{task}.py").write_text('"""Feature."""\n\nx = 1\n', encoding="utf-8")
    result = commit_workspace_changes(
        workspace_path=workspace_path,
        message=f"Feature: {task}",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )
    store.connection.commit()
    assert result["status"] == "committed", result
    return workspace, f"codex/{task}"


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_merge_lands_the_work_branch_on_a_non_checked_out_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El merge aterriza en una base NO checked-out vía worktree temporal, sin tocar el árbol principal."""
    from local_control_center.workspaces_projects.git_worktrees import merge_work_branch_into_base

    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "land-repo"
    create_git_repo(repo)
    assert run_git(["branch", "devbase"], cwd=repo).returncode == 0
    project = store.create_project(name="Land", path=repo, template_id="other")
    _workspace, work_branch = _committed_work_branch(store, tmp_path, repo, project, task="land-task")

    result = merge_work_branch_into_base(
        repo_path=repo,
        work_branch=work_branch,
        base_branch="devbase",
        message=f"Merge {work_branch} into devbase",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
    )
    store.connection.commit()

    assert result["status"] == "merged", result
    show = run_git(["show", "devbase:land-task.py"], cwd=repo)
    assert show.returncode == 0 and "x = 1" in show.stdout
    # El árbol principal no cambió de rama ni quedó sucio.
    assert run_git(["status", "--porcelain"], cwd=repo).stdout.strip() == ""
    # La rama de aterrizaje temporal no queda colgando.
    branches = run_git(["branch", "--list", "aido/landing/*"], cwd=repo).stdout
    assert branches.strip() == ""


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_merge_lands_on_the_checked_out_base_and_conflict_aborts_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge directo cuando la base está checked-out; un conflicto aborta sin dejar el árbol a medias."""
    from local_control_center.workspaces_projects.git_worktrees import (
        git_current_branch,
        merge_work_branch_into_base,
    )

    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "land-direct-repo"
    create_git_repo(repo)
    base = git_current_branch(repo)
    assert base
    project = store.create_project(name="LandDirect", path=repo, template_id="other")
    _workspace, work_branch = _committed_work_branch(store, tmp_path, repo, project, task="direct-task")

    result = merge_work_branch_into_base(
        repo_path=repo,
        work_branch=work_branch,
        base_branch=base,
        message=f"Merge {work_branch}",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
    )
    assert result["status"] == "merged", result
    assert (repo / "direct-task.py").exists()

    # Conflicto: la base y una nueva rama de trabajo editan la misma línea de README.md.
    _conflict_ws, conflict_branch = _committed_work_branch(
        store, tmp_path, repo, project, task="conflict-task"
    )
    conflict_path = Path(_conflict_ws["path"]) / "README.md"
    conflict_path.write_text("work version\n", encoding="utf-8")
    from local_control_center.workspaces_projects.git_worktrees import commit_workspace_changes

    commit_workspace_changes(
        workspace_path=Path(_conflict_ws["path"]),
        message="work edit",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=_conflict_ws["id"],
    )
    (repo / "README.md").write_text("base version\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=repo).returncode == 0
    assert (
        run_git(
            ["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "base edit"], cwd=repo
        ).returncode
        == 0
    )

    conflicted = merge_work_branch_into_base(
        repo_path=repo,
        work_branch=conflict_branch,
        base_branch=base,
        message="conflicting merge",
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
    )
    assert conflicted["status"] == "merge_conflict", conflicted
    assert run_git(["status", "--porcelain"], cwd=repo).stdout.strip() == ""


@pytest.mark.skipif(not git_available(), reason="git CLI is not available")
def test_push_branch_publishes_to_the_configured_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El push publica la rama en el remoto configurado (bare local), auditado por policy."""
    from local_control_center.workspaces_projects.git_worktrees import (
        git_remote_exists,
        push_branch_to_remote,
    )

    store, _client, _headers = make_app(tmp_path, monkeypatch)
    repo = tmp_path / "push-repo"
    create_git_repo(repo)
    bare = tmp_path / "remote.git"
    assert run_git(["init", "--bare", str(bare)], cwd=tmp_path).returncode == 0
    assert run_git(["remote", "add", "origin", str(bare)], cwd=repo).returncode == 0
    project = store.create_project(name="Push", path=repo, template_id="other")
    _workspace, work_branch = _committed_work_branch(store, tmp_path, repo, project, task="push-task")

    assert git_remote_exists(
        repo_path=repo, remote="origin", connection=store.connection, root=tmp_path, project_id=project["id"]
    )
    assert not git_remote_exists(
        repo_path=repo, remote="missing", connection=store.connection, root=tmp_path, project_id=project["id"]
    )

    result = push_branch_to_remote(
        repo_path=repo,
        remote="origin",
        branch=work_branch,
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
    )
    store.connection.commit()

    assert result["status"] == "pushed", result
    assert run_git(["rev-parse", "--verify", work_branch], cwd=bare).returncode == 0
