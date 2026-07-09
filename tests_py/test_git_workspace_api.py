from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.security_policy.git_command_runner import git_available, run_git
from tests_py.control_plane_fixture import ControlPlaneFixture

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is required for git workspace tests")


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


def init_git_project(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    init = run_git(["init", "--initial-branch", "main"], cwd=path)
    if init.returncode != 0:
        assert run_git(["init"], cwd=path).returncode == 0
        assert run_git(["checkout", "-b", "main"], cwd=path).returncode == 0
    assert run_git(["config", "user.email", "aido-tests@example.invalid"], cwd=path).returncode == 0
    assert run_git(["config", "user.name", "AIDO Tests"], cwd=path).returncode == 0
    (path / "README.md").write_text("# AIDO git workspace\n", encoding="utf-8")
    assert run_git(["add", "README.md"], cwd=path).returncode == 0
    assert run_git(["commit", "-m", "Initial commit"], cwd=path).returncode == 0


def create_git_project(store: ControlPlaneFixture, tmp_path: Path, *, name: str = "repo") -> dict[str, Any]:
    project_path = tmp_path / name
    init_git_project(project_path)
    return store.create_project(name=name, path=project_path, template_id="other")


def create_plain_project(
    store: ControlPlaneFixture,
    tmp_path: Path,
    *,
    name: str = "plain",
    template_id: str = "other",
) -> dict[str, Any]:
    project_path = tmp_path / name
    project_path.mkdir(parents=True, exist_ok=True)
    return store.create_project(name=name, path=project_path, template_id=template_id)


def test_git_init_completed_for_project_without_git_creates_default_branch_and_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_plain_project(store, tmp_path, template_id="python-fastapi")
    project_path = Path(project["path"])

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/init",
        headers=headers,
        json={"defaultBranch": "dev"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["currentBranch"] == "dev"
    assert body["defaultBranch"] == "dev"
    assert body["commitCreated"] is False
    assert body["gitignoreCreated"] is True
    assert (project_path / ".git").exists()
    gitignore = (project_path / ".gitignore").read_text(encoding="utf-8")
    assert ".env*" in gitignore
    assert "__pycache__/" in gitignore
    assert run_git(["branch", "--show-current"], cwd=project_path).stdout.strip() == "dev"
    assert run_git(["rev-parse", "--verify", "HEAD"], cwd=project_path).returncode != 0
    assert not any("commit" in call["payload"].get("command", "") for call in store.agents.list_agent_tool_calls())


def test_git_init_defaults_to_dev_when_branch_not_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_plain_project(store, tmp_path)
    project_path = Path(project["path"])

    response = client.post(f"/api/v1/projects/{project['id']}/git/init", headers=headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["defaultBranch"] == "dev"
    assert body["currentBranch"] == "dev"
    assert run_git(["branch", "--show-current"], cwd=project_path).stdout.strip() == "dev"


def test_git_init_preserves_existing_gitignore_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_plain_project(store, tmp_path, template_id="python-fastapi")
    project_path = Path(project["path"])
    sentinel = "# hand-written ignore\ncustom-secret.txt\n"
    (project_path / ".gitignore").write_text(sentinel, encoding="utf-8")

    response = client.post(f"/api/v1/projects/{project['id']}/git/init", headers=headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["gitignoreCreated"] is False
    # A pre-existing .gitignore must survive init byte-for-byte: no overwrite, no append.
    assert (project_path / ".gitignore").read_text(encoding="utf-8") == sentinel


def test_git_remote_add_persists_sanitized_metadata_and_uses_git_remote_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/remotes",
        headers=headers,
        json={"name": "origin", "url": "git@github.com:aido/example.git"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["remote"]["name"] == "origin"
    assert body["remote"]["url"] == "git@github.com:aido/example.git"
    assert body["remote"]["host"] == "github.com"
    assert "token" not in str(body["remote"]).lower()
    assert run_git(["remote", "get-url", "origin"], cwd=project_path).stdout.strip() == (
        "git@github.com:aido/example.git"
    )
    assert any("remote add origin" in call["payload"].get("command", "") for call in store.agents.list_agent_tool_calls())


def test_git_remote_with_embedded_token_is_blocked_before_git_remote_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/remotes",
        headers=headers,
        json={"name": "origin", "url": "https://ghp_secret@example.com/aido/repo.git"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert "token" in body["reason"].lower() or "credential" in body["reason"].lower()
    assert run_git(["remote"], cwd=project_path).stdout.strip() == ""
    assert not any("remote add origin" in call["payload"].get("command", "") for call in store.agents.list_agent_tool_calls())


def test_git_branch_policy_suggests_branch_from_intent_and_creates_from_selected_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-policy/apply",
        headers=headers,
        json={"intent": "Fix Git init for new projects", "selectedBase": "main", "createBranch": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["selectedBase"] == "main"
    # Suggested name comes from the deterministic IntentClassifier, so it carries the classified
    # intent ("fix" -> bugfix) as a prefix over the slugified intent text.
    assert body["suggestedBranchName"] == "codex/bugfix-fix-git-init-for-new-projects"
    assert body["targetBranch"] == "codex/bugfix-fix-git-init-for-new-projects"
    assert body["created"] is True
    branches = client.get(f"/api/v1/projects/{project['id']}/git/branches").json()
    assert "codex/bugfix-fix-git-init-for-new-projects" in branches["localBranches"]


def test_git_branch_policy_blocks_main_and_master_as_work_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-policy/apply",
        headers=headers,
        json={
            "intent": "Implement directly on main",
            "selectedBase": "main",
            "branchName": "main",
            "createBranch": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["targetBranch"] == "main"
    assert "protected" in body["reason"].lower()


def test_git_branch_policy_allows_protected_branch_with_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/branch-policy/apply",
        headers=headers,
        json={
            "intent": "Work directly on main",
            "selectedBase": "main",
            "branchName": "main",
            "createBranch": True,
            "allowProtected": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["targetBranch"] == "main"
    assert body["created"] is False
    assert "override" in body["reason"].lower()
    # The override must NOT create a branch nor leave the checkout on anything but main.
    assert run_git(["branch", "--show-current"], cwd=project_path).stdout.strip() == "main"
    local_branches = {
        line.strip().lstrip("* ").strip()
        for line in run_git(["branch"], cwd=project_path).stdout.splitlines()
        if line.strip()
    }
    assert local_branches == {"main"}


def test_git_status_detects_current_branch_and_records_broker_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)

    response = client.get(f"/api/v1/projects/{project['id']}/git/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["currentBranch"] == "main"
    assert body["dirty"] is False
    assert body["changedFiles"] == []
    assert body["untrackedFiles"] == []
    assert body["stagedFiles"] == []
    assert body["lastCommit"]["subject"] == "Initial commit"
    assert body["worktrees"]
    assert store.security.list_decisions(project_id=project["id"])
    assert any(
        call["toolName"] == "shell" and call["status"] == "completed"
        for call in store.agents.list_agent_tool_calls()
    )


def test_git_status_detects_branch_dirty_state_and_sanitizes_existing_remotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])
    assert run_git(["checkout", "-b", "feature/status-snapshot"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# AIDO git workspace\nchanged\n", encoding="utf-8")
    secret_remote = "https://oauth2:glpat-abcdefghijklmnop@gitlab.com/aido/repo.git"
    assert run_git(["remote", "add", "origin", secret_remote], cwd=project_path).returncode == 0

    response = client.get(f"/api/v1/projects/{project['id']}/git/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["currentBranch"] == "feature/status-snapshot"
    assert body["dirty"] is True
    assert body["changedFiles"] == ["README.md"]
    assert body["untrackedFiles"] == []
    assert body["stagedFiles"] == []
    assert {(remote["name"], remote["direction"]) for remote in body["remotes"]} == {
        ("origin", "fetch"),
        ("origin", "push"),
    }
    remote_urls = {remote["url"] for remote in body["remotes"]}
    assert remote_urls == {"https://gitlab.com/aido/repo.git"}
    assert "glpat-" not in str(body)
    assert "oauth2:" not in str(body)


def test_git_status_rejects_project_nested_inside_another_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project folder nested inside a PARENT repo must report 'not connected', not leak its branches.

    Regression: git walks up to the nearest `.git`, so without an own-repo check the control plane
    listed the parent repository's branches (e.g. AIDO's) for a project that owns no repo of its own.
    """
    store, client, _headers = create_client(tmp_path, monkeypatch)
    parent = tmp_path / "parent_repo"
    init_git_project(parent)
    assert run_git(["branch", "leaked-parent-branch"], cwd=parent).returncode == 0
    nested = parent / "nested_project"
    nested.mkdir(parents=True, exist_ok=True)
    project = store.create_project(name="nested", path=nested, template_id="other")

    status = client.get(f"/api/v1/projects/{project['id']}/git/status").json()
    branches = client.get(f"/api/v1/projects/{project['id']}/git/branches").json()

    assert status["status"] == "configuration_required"
    assert status["reason"] == "Project path is not a Git repository."
    assert status["currentBranch"] == ""
    assert branches["localBranches"] == []
    assert "leaked-parent-branch" not in branches["localBranches"]
    assert "main" not in branches["localBranches"]


def test_git_status_categorizes_changed_untracked_and_staged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])
    (project_path / "README.md").write_text("# AIDO git workspace\nchanged\n", encoding="utf-8")
    (project_path / "notes.txt").write_text("untracked\n", encoding="utf-8")
    (project_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    assert run_git(["add", "staged.txt"], cwd=project_path).returncode == 0

    response = client.get(f"/api/v1/projects/{project['id']}/git/status")

    assert response.status_code == 200
    body = response.json()
    assert body["dirty"] is True
    assert "README.md" in body["changedFiles"]
    assert body["untrackedFiles"] == ["notes.txt"]
    assert body["stagedFiles"] == ["staged.txt"]


def test_git_branches_reuse_status_snapshot_without_extra_git_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branches endpoint must reuse the status() snapshot, not re-shell `git branch` again.

    Perf regression guard: `status()` already brokers `git branch --format` and
    `git branch --remotes`, so `branches()` reformats that result. Hitting /git/branches must
    broker exactly the same set of git commands as a single /git/status call — no duplicates —
    while every command stays audited (broker traces + policy decisions travel in the response).
    """
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    assert run_git(["branch", "feature/reuse"], cwd=Path(project["path"])).returncode == 0

    base = len(store.agents.list_agent_tool_calls())
    status = client.get(f"/api/v1/projects/{project['id']}/git/status").json()
    after_status = len(store.agents.list_agent_tool_calls())
    branches = client.get(f"/api/v1/projects/{project['id']}/git/branches").json()
    after_branches = len(store.agents.list_agent_tool_calls())

    status_commands = after_status - base
    branch_commands = after_branches - after_status

    assert status["status"] == "completed"
    assert branches["status"] == "completed"
    assert status_commands > 0
    assert branch_commands == status_commands
    assert set(branches["localBranches"]) == {"main", "feature/reuse"}
    assert branches["currentBranch"] == "main"
    assert branches["toolCalls"]
    assert branches["policyDecisionIds"]


def test_git_branch_can_be_created_from_current_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)

    created = client.post(
        f"/api/v1/projects/{project['id']}/git/branches",
        headers=headers,
        json={"name": "feature/git-workspace"},
    )
    branches = client.get(f"/api/v1/projects/{project['id']}/git/branches")

    assert created.status_code == 201
    assert created.json()["status"] == "completed"
    assert created.json()["branch"] == "feature/git-workspace"
    assert "feature/git-workspace" in branches.json()["localBranches"]
    assert branches.json()["currentBranch"] == "main"


def test_git_checkout_blocks_dirty_tree_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])
    assert run_git(["branch", "feature/clean"], cwd=project_path).returncode == 0
    (project_path / "README.md").write_text("# dirty\n", encoding="utf-8")

    response = client.post(
        f"/api/v1/projects/{project['id']}/git/checkout",
        headers=headers,
        json={"branch": "feature/clean"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert "dirty" in body["reason"].lower()
    assert run_git(["branch", "--show-current"], cwd=project_path).stdout.strip() == "main"


def test_git_diff_returns_real_diff_and_changed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    project_path = Path(project["path"])
    (project_path / "README.md").write_text("# AIDO git workspace\nnew line\n", encoding="utf-8")

    response = client.get(f"/api/v1/projects/{project['id']}/git/diff")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert "new line" in body["diff"]
    assert body["changedFiles"] == ["README.md"]


def test_git_gitleaks_unavailable_returns_configuration_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))

    response = client.post(f"/api/v1/projects/{project['id']}/git/gitleaks/scan", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "configuration_required"
    assert body["gitleaks"]["executable"] is False
    assert "not found" in body["reason"].lower()


def write_fake_gitleaks(bin_dir: Path, script: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    implementation = bin_dir / "gitleaks_impl.py"
    implementation.write_text(script, encoding="utf-8")
    if os.name == "nt":
        command = bin_dir / "gitleaks.cmd"
        command.write_text(f'@echo off\r\n"{sys.executable}" "{implementation}" %*\r\n', encoding="utf-8")
    else:
        command = bin_dir / "gitleaks"
        command.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
        command.chmod(0o755)


def test_git_gitleaks_failure_blocks_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    fake_bin = tmp_path / "fake-bin"
    write_fake_gitleaks(
        fake_bin,
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n"
        "report = Path(sys.argv[sys.argv.index('--report-path') + 1])\n"
        "report.write_text(json.dumps([{'RuleID': 'generic-api-key', 'Description': 'secret', 'File': 'settings.ini', 'StartLine': 1}]), encoding='utf-8')\n"
        "raise SystemExit(1)\n",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    response = client.post(f"/api/v1/projects/{project['id']}/git/gitleaks/scan", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["gitleaks"]["status"] == "blocked"
    assert body["gitleaks"]["findingCount"] == 1
    assert body["deliveryBlocked"] is True
