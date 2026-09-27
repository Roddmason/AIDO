"""Gestor de ramas gitflow y apertura de carpeta del proyecto sobre repos Git reales temporales.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.app import create_app
from local_control_center.process_supervision import desktop_launcher
from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.security_policy.policy_engine import evaluate_action
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.execution_client import CompletedExecutionClient as TestClient

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is required for branch manager tests")


def git(cwd: Path, *args: str) -> str:
    result = run_git(list(args), cwd=cwd)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout


def commit(cwd: Path, name: str, content: str, message: str) -> None:
    (cwd / name).write_text(content, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-m", message)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from local_control_center.host_resources.models import ResourceSnapshot

    monkeypatch.setattr(
        "local_control_center.host_resources.probes.HostResourceProbe.sample",
        lambda *args, **kwargs: ResourceSnapshot.test_snapshot(),
    )
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    with ExitStack() as stack:
        store = stack.enter_context(
            closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
        )
        store.init()
        client = stack.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}
        yield store, client, headers


def build_repo(root: Path, *, with_remote: bool = False) -> Path:
    """Repo gitflow: main + dev y ramas merged / squash / cherry-pick / wip / sin prefijo."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "--initial-branch", "main")
    git(repo, "config", "user.email", "aido-tests@example.invalid")
    git(repo, "config", "user.name", "AIDO Tests")
    commit(repo, "README.md", "# repo\n", "Initial commit")
    git(repo, "checkout", "-b", "dev")
    commit(repo, "dev.txt", "dev\n", "Dev work")

    git(repo, "checkout", "-b", "feature/merged", "dev")
    commit(repo, "merged.txt", "merged\n", "Merged feature")
    git(repo, "checkout", "dev")
    git(repo, "merge", "--no-ff", "-m", "Merge feature/merged", "feature/merged")

    git(repo, "checkout", "-b", "feature/squashed", "dev")
    commit(repo, "squash-a.txt", "a\n", "Squash part A")
    commit(repo, "squash-b.txt", "b\n", "Squash part B")
    git(repo, "checkout", "dev")
    git(repo, "merge", "--squash", "feature/squashed")
    git(repo, "commit", "-m", "Squash feature/squashed")

    git(repo, "checkout", "-b", "bugfix/picked", "dev")
    commit(repo, "picked.txt", "picked\n", "Picked fix")
    picked = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "dev")
    commit(repo, "after.txt", "after\n", "Dev moves on")
    git(repo, "cherry-pick", picked)

    git(repo, "checkout", "-b", "feature/wip", "dev")
    commit(repo, "wip.txt", "wip\n", "Work in progress")
    git(repo, "checkout", "-b", "quick-fix-login", "dev")
    commit(repo, "login.txt", "login\n", "Quick login fix")
    git(repo, "checkout", "-b", "feature/in-worktree", "dev")
    git(repo, "checkout", "main")
    git(repo, "worktree", "add", str(root / "wt"), "feature/in-worktree")

    if with_remote:
        remote = root / "remote.git"
        git(root, "init", "--bare", "--initial-branch", "main", str(remote))
        git(repo, "remote", "add", "origin", str(remote))
        for branch in ("main", "dev", "feature/merged", "feature/gone"):
            if branch == "feature/gone":
                git(repo, "branch", "feature/gone", "dev")
            git(repo, "push", "-u", "origin", branch)
    return repo


def scan(client: TestClient, headers: dict[str, str], project_id: str) -> dict[str, Any]:
    response = client.post(f"/api/v1/projects/{project_id}/git/branch-manager/scan", headers=headers, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed", body
    return body


def by_name(body: dict[str, Any], name: str, kind: str = "local") -> dict[str, Any]:
    return next(item for item in body["branches"] if item["name"] == name and item["kind"] == kind)


def local_branches(repo: Path) -> set[str]:
    return set(git(repo, "branch", "--format=%(refname:short)").split())


def test_inventory_detects_merged_squash_and_gitflow_health(harness, tmp_path: Path) -> None:
    store, client, headers = harness
    repo = build_repo(tmp_path)
    project = store.create_project(name="repo", path=repo, template_id="other")

    body = scan(client, headers, project["id"])

    assert body["integrationBranch"] == "dev"
    assert body["integrationExists"] is True
    assert body["currentBranch"] == "main"
    assert by_name(body, "feature/merged")["mergeState"] == "merged"
    assert by_name(body, "feature/merged")["deletable"] is True
    assert by_name(body, "feature/merged")["requiresForce"] is False
    squashed = by_name(body, "feature/squashed")
    assert squashed["mergeState"] == "squash_probable"
    assert squashed["mergeEvidence"] == "merge-tree"
    assert squashed["requiresForce"] is True
    assert by_name(body, "bugfix/picked")["mergeState"] == "squash_probable"
    assert by_name(body, "bugfix/picked")["mergeEvidence"] == "git cherry"
    wip = by_name(body, "feature/wip")
    assert wip["mergeState"] == "not_merged"
    assert wip["ahead"] == 1 and wip["behind"] == 0
    assert wip["type"] == "feature"
    other = by_name(body, "quick-fix-login")
    assert other["type"] == "other"
    assert other["suggestedName"] == "bugfix/quick-fix-login"
    assert by_name(body, "dev")["protectedReason"] == "integration_branch"
    assert by_name(body, "main")["protectedReason"] == "gitflow_mainline"
    worktree = by_name(body, "feature/in-worktree")
    assert worktree["protected"] is True
    assert worktree["protectedReason"] == "checked_out_in_worktree"
    assert worktree["worktreePath"]
    kinds = {issue["kind"]: issue for issue in body["health"]["issues"]}
    assert kinds["merged_not_deleted"]["branches"] == ["feature/merged"]
    assert set(kinds["squash_probable"]["branches"]) == {"feature/squashed", "bugfix/picked"}
    assert kinds["non_gitflow"]["branches"] == ["quick-fix-login"]
    assert body["policyDecisionIds"]
    assert all(call["toolCallStatus"] for call in body["toolCalls"])

    snapshot = client.get(f"/api/v1/projects/{project['id']}/git/branch-manager").json()
    assert snapshot["refreshRequired"] is False
    assert snapshot["snapshotAt"]
    assert len(snapshot["branches"]) == len(body["branches"])


def test_delete_refuses_protected_worktree_and_unforced_squash(harness, tmp_path: Path) -> None:
    store, client, headers = harness
    repo = build_repo(tmp_path)
    project = store.create_project(name="repo", path=repo, template_id="other")

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/delete",
        headers=headers,
        json={
            "branches": [
                "dev",
                "main",
                "feature/in-worktree",
                "feature/squashed",
                "feature/wip",
                "feature/merged",
            ]
        },
    )

    assert response.status_code == 200, response.text
    results = {item["branch"]: item for item in response.json()["results"]}
    assert results["dev"]["status"] == "skipped"
    assert results["dev"]["reason"] == "protected:integration_branch"
    assert results["main"]["reason"] == "protected:gitflow_mainline"
    assert results["feature/in-worktree"]["reason"] == "protected:checked_out_in_worktree"
    assert results["feature/squashed"]["reason"] == "squash_probable_requires_force"
    assert results["feature/wip"]["reason"] == "not_merged_requires_force"
    # HEAD is main, so `git branch -d` alone would refuse; the verified merge into dev allows -D.
    assert results["feature/merged"]["status"] == "deleted"
    assert results["feature/merged"]["forced"] is False
    remaining = local_branches(repo)
    assert "feature/merged" not in remaining
    assert {"dev", "main", "feature/in-worktree", "feature/squashed", "feature/wip"} <= remaining
    assert response.json()["summary"] == {"done": 1, "skipped": 5, "failed": 0}


def test_force_delete_only_for_explicitly_listed_branches(harness, tmp_path: Path) -> None:
    store, client, headers = harness
    repo = build_repo(tmp_path)
    project = store.create_project(name="repo", path=repo, template_id="other")

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/delete",
        headers=headers,
        json={"branches": ["feature/squashed", "feature/wip"], "forceBranches": ["feature/wip"]},
    )

    results = {item["branch"]: item for item in response.json()["results"]}
    assert results["feature/wip"]["status"] == "deleted"
    assert results["feature/wip"]["forced"] is True
    assert results["feature/squashed"]["status"] == "skipped"
    assert "feature/wip" not in local_branches(repo)
    assert "feature/squashed" in local_branches(repo)


def test_policy_refuses_protected_names_even_if_the_service_is_bypassed() -> None:
    base = {
        "agentId": "git_workspace_agent",
        "role": "devops_engineer",
        "permissionProfile": "dev_safe",
        "tool": "shell",
        "operation": "git_workspace_command",
        "workspaceId": "workspace-1",
        "workspacePath": "/tmp/repo",
        "path": "/tmp/repo",
        "agentRunId": "run-1",
    }

    def decide(argv: list[str], git_operation: str, *, network: bool = False) -> str:
        return evaluate_action(
            {
                **base,
                "command": " ".join(argv),
                "commandArgv": argv,
                "gitOperation": git_operation,
                "networkRequired": network,
            }
        )["decision"]

    assert decide(["git", "branch", "-D", "develop"], "delete_branch") == "deny"
    assert (
        decide(["git", "push", "origin", "--delete", "dev"], "delete_remote_branch", network=True) == "deny"
    )
    assert decide(["git", "push", "origin", "--delete", "feature/x"], "delete_remote_branch") == "deny"
    assert (
        decide(["git", "push", "--force", "origin", "feature/x"], "delete_remote_branch", network=True)
        == "deny"
    )
    assert decide(["git", "branch", "-m", "wip", "random"], "rename_branch") == "deny"
    assert decide(["git", "branch", "-m", "main", "feature/main"], "rename_branch") == "deny"
    assert decide(["git", "fetch", "origin"], "prune_remote_refs", network=True) == "deny"
    assert decide(["git", "merge-tree", "dev", "feature/x"], "branch_inventory") == "deny"
    assert decide(["git", "branch", "-m", "wip", "feature/wip"], "rename_branch") == "allow"
    assert decide(["git", "fetch", "--prune", "origin"], "prune_remote_refs", network=True) == "allow"


def test_remote_delete_is_opt_in_and_prune_drops_gone_refs(harness, tmp_path: Path) -> None:
    store, client, headers = harness
    repo = build_repo(tmp_path, with_remote=True)
    remote = tmp_path / "remote.git"
    project = store.create_project(name="repo", path=repo, template_id="other")

    without_remote = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/delete",
        headers=headers,
        json={"branches": ["feature/merged"], "remoteBranches": ["origin/feature/merged"]},
    ).json()
    remote_result = next(item for item in without_remote["results"] if item["kind"] == "remote")
    assert remote_result["status"] == "skipped"
    assert remote_result["reason"] == "remote_delete_not_enabled"
    assert "feature/merged" in git(remote, "branch", "--format=%(refname:short)").split()

    with_remote = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/delete",
        headers=headers,
        json={"remoteBranches": ["origin/feature/merged", "origin/dev"], "deleteRemote": True},
    ).json()
    results = {item["branch"]: item for item in with_remote["results"]}
    assert results["origin/feature/merged"]["status"] == "deleted", results
    assert results["origin/dev"]["status"] == "skipped"
    assert results["origin/dev"]["reason"] == "protected:integration_branch"
    remote_heads = set(git(remote, "branch", "--format=%(refname:short)").split())
    assert "feature/merged" not in remote_heads
    assert "dev" in remote_heads

    git(remote, "branch", "-D", "feature/gone")
    before = scan(client, headers, project["id"])
    assert by_name(before, "origin/feature/gone", "remote")
    pruned = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/prune", headers=headers, json={}
    ).json()
    assert pruned["results"][0]["status"] == "pruned"
    assert "origin/feature/gone" in pruned["results"][0]["detail"]
    after = scan(client, headers, project["id"])
    assert not any(item["name"] == "origin/feature/gone" for item in after["branches"])
    assert by_name(after, "feature/gone")["upstream"]["status"] == "gone"
    assert any(issue["kind"] == "upstream_gone" for issue in after["health"]["issues"])


def test_rename_only_local_branches_without_upstream_to_gitflow_prefix(harness, tmp_path: Path) -> None:
    store, client, headers = harness
    repo = build_repo(tmp_path, with_remote=True)
    project = store.create_project(name="repo", path=repo, template_id="other")
    url = f"/api/v1/projects/{project['id']}/git/branch-manager/rename"

    bad_prefix = client.post(
        url, headers=headers, json={"branch": "quick-fix-login", "newName": "random/x"}
    ).json()
    tracked = client.post(
        url, headers=headers, json={"branch": "feature/merged", "newName": "feature/merged-2"}
    ).json()
    protected = client.post(url, headers=headers, json={"branch": "dev", "newName": "feature/dev"}).json()
    renamed = client.post(
        url, headers=headers, json={"branch": "quick-fix-login", "newName": "bugfix/quick-fix-login"}
    ).json()

    assert bad_prefix["results"][0]["reason"] == "invalid_gitflow_name"
    assert tracked["results"][0]["reason"] == "has_upstream"
    assert protected["results"][0]["reason"] == "protected:integration_branch"
    assert renamed["results"][0]["status"] == "renamed"
    branches = local_branches(repo)
    assert "bugfix/quick-fix-login" in branches
    assert "quick-fix-login" not in branches


def test_branch_manager_mutations_require_write_token(harness, tmp_path: Path) -> None:
    store, client, _headers = harness
    repo = build_repo(tmp_path)
    project = store.create_project(name="repo", path=repo, template_id="other")

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/delete", json={"branches": ["feature/merged"]}
    )

    assert response.status_code == 403
    assert "feature/merged" in local_branches(repo)


class FakeProcess:
    pid = 4242

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_open_folder_uses_registered_path_and_fixed_argv(
    harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = harness
    project_path = tmp_path / "proj"
    project_path.mkdir()
    project = store.create_project(name="proj", path=project_path, template_id="other")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        calls.append((argv, kwargs))
        return FakeProcess()

    real = desktop_launcher.open_in_file_manager
    monkeypatch.setattr(
        "local_control_center.projects.api.open_in_file_manager",
        lambda path: real(path, popen_factory=fake_popen, platform="win32"),
    )

    denied = client.post(f"/api/v1/projects/{project['id']}/open-folder")
    opened = client.post(
        f"/api/v1/projects/{project['id']}/open-folder", headers=headers, json={"path": "/etc"}
    )
    missing = client.post("/api/v1/projects/project-missing/open-folder", headers=headers)

    assert denied.status_code == 403
    assert opened.status_code == 200, opened.text
    assert opened.json()["launcher"] == "explorer.exe"
    assert len(calls) == 1
    argv, options = calls[0]
    assert Path(argv[0]).name == "explorer.exe"
    assert argv[1:] == [str(project_path.resolve())]
    assert options["shell"] is False
    assert "/etc" not in argv
    assert missing.status_code == 404


def test_open_folder_reports_missing_directory(
    harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = harness
    project_path = tmp_path / "gone"
    project_path.mkdir()
    project = store.create_project(name="gone", path=project_path, template_id="other")
    project_path.rmdir()
    launched: list[list[str]] = []
    monkeypatch.setattr(
        "local_control_center.projects.api.open_in_file_manager",
        lambda path: desktop_launcher.open_in_file_manager(
            path, popen_factory=lambda argv, **_: launched.append(argv) or FakeProcess()
        ),
    )

    response = client.post(f"/api/v1/projects/{project['id']}/open-folder", headers=headers)

    assert response.status_code == 409
    assert "no longer exists" in response.json()["detail"]
    assert launched == []


def test_file_manager_argv_per_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert desktop_launcher.file_manager_argv(tmp_path, platform="darwin") == ["/usr/bin/open", str(tmp_path)]
    monkeypatch.setattr(desktop_launcher.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert desktop_launcher.file_manager_argv(tmp_path, platform="linux") == [
        "/usr/bin/xdg-open",
        str(tmp_path),
    ]
    monkeypatch.setattr(desktop_launcher.shutil, "which", lambda name: None)
    with pytest.raises(desktop_launcher.FileManagerUnavailable):
        desktop_launcher.file_manager_argv(tmp_path, platform="linux")
    with pytest.raises(ValueError):
        desktop_launcher.file_manager_argv(Path("relative/dir"), platform="darwin")


def test_merged_aido_worktree_is_removed_then_its_branch_can_be_deleted(harness, tmp_path: Path) -> None:
    from local_control_center.workspaces_projects.repository import WorkspacesRepository

    store, client, headers = harness
    repo = build_repo(tmp_path)
    project = store.create_project(name="repo", path=repo, template_id="other")
    repository = WorkspacesRepository(store.connection, root=tmp_path)
    merged_ws = repository.allocate_workspace(
        project_id=project["id"],
        task_id="product-loop-abc",
        agent_id="developer",
        base_branch="dev",
        branch_name="codex/product-loop-abc",
    )
    live_ws = repository.allocate_workspace(
        project_id=project["id"],
        task_id="product-loop-def",
        agent_id="developer",
        base_branch="dev",
        branch_name="codex/product-loop-def",
    )
    store.connection.commit()
    assert merged_ws["isolationType"] == "git_worktree"
    live_path = Path(live_ws["path"])
    commit(live_path, "live.txt", "live\n", "Live work")

    body = scan(client, headers, project["id"])
    merged_branch = by_name(body, "codex/product-loop-abc")
    assert merged_branch["type"] == "aido"
    assert merged_branch["mergeState"] == "merged"
    assert merged_branch["protectedReason"] == "checked_out_in_worktree"
    assert merged_branch["aidoWorkspaces"][0]["workspaceId"] == merged_ws["id"]
    merged_worktrees = next(issue for issue in body["health"]["issues"] if issue["kind"] == "merged_worktree")
    assert merged_worktrees["branches"] == ["codex/product-loop-abc"]

    removed = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/worktrees/remove",
        headers=headers,
        json={"workspaceIds": [merged_ws["id"], live_ws["id"]]},
    ).json()
    results = {item["branch"]: item for item in removed["results"]}
    assert results["codex/product-loop-abc"]["status"] == "removed"
    assert results["codex/product-loop-def"]["status"] == "skipped"
    assert results["codex/product-loop-def"]["reason"] == "branch_not_merged"
    assert not Path(merged_ws["path"]).exists()
    assert live_path.exists()

    deleted = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-manager/delete",
        headers=headers,
        json={"branches": ["codex/product-loop-abc"]},
    ).json()
    assert deleted["results"][0]["status"] == "deleted"
    assert "codex/product-loop-abc" not in local_branches(repo)
