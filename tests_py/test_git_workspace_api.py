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
