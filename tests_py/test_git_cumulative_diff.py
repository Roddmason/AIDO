"""Diff acumulado de la rama del hilo contra su base: evidencia de todas las historias del loop.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.workspaces_projects import git_worktrees
from local_control_center.workspaces_projects.git_worktrees import (
    _usable_base_ref,
    capture_cumulative_diff,
)
from tests_py.test_git_diff_capture import _workspace_with_repo
from tests_py.test_workspace_isolation_contract import make_app as make_app

pytestmark = [
    pytest.mark.skipif(not git_available(), reason="git CLI is not available"),
    pytest.mark.usefixtures("controlled_domain_host"),
]


def _commit(worktree: Path, relative: str, content: str) -> None:
    target = worktree / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    assert run_git(["add", relative], cwd=worktree).returncode == 0
    commit = run_git(
        [
            "-c",
            "user.name=AIDO Tests",
            "-c",
            "user.email=aido@example.test",
            "commit",
            "-m",
            f"story {relative}",
        ],
        cwd=worktree,
    )
    assert commit.returncode == 0, commit.stderr


def _capture(
    store: Any, tmp_path: Path, project: dict[str, Any], workspace: dict[str, Any], base_refs: list[str]
) -> dict[str, Any]:
    return capture_cumulative_diff(
        Path(workspace["path"]),
        base_refs=base_refs,
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )


def test_cumulative_diff_spans_every_story_commit_since_the_base(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-two")
    worktree = Path(workspace["path"])
    _commit(worktree, "src/story_one.py", "ONE = 1\n")
    _commit(worktree, "src/story_two.py", "TWO = 2\n")

    diff = _capture(store, tmp_path, project, workspace, ["missing-base", "devbase"])

    assert diff["state"] == "captured"
    assert diff["baseRef"] == "devbase"
    assert diff["nameOnly"] == ["src/story_one.py", "src/story_two.py"]
    assert "ONE = 1" in diff["patchFull"]
    assert "TWO = 2" in diff["patchFull"]
    assert diff["headCommit"]
    assert diff["policyDecisionIds"]


def test_cumulative_diff_fails_closed_when_no_base_ref_resolves(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-no-base")

    diff = _capture(store, tmp_path, project, workspace, ["missing-base", "HEAD", "-evil", ""])

    assert diff["state"] == "capture_failed"
    assert "base" in diff["stderr"].lower()


def test_cumulative_diff_without_story_commits_is_captured_and_empty(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-empty")

    diff = _capture(store, tmp_path, project, workspace, ["devbase"])

    assert diff["state"] == "captured"
    assert diff["nameOnly"] == []
    assert diff["patchFull"] == ""
    assert diff["status"] == []


@pytest.mark.parametrize(
    "ref",
    ["HEAD", "@", "HEAD^", "HEAD~2", "base@{upstream}", "", "-evil", "a..b", "a b"],
)
def test_usable_base_ref_rejects_head_equivalents_and_relative_forms(ref: str) -> None:
    assert _usable_base_ref(ref) is False


@pytest.mark.parametrize("ref", ["devbase", "missing-base", "release/1.0", "a1b2c3d"])
def test_usable_base_ref_accepts_plain_branch_and_sha_names(ref: str) -> None:
    assert _usable_base_ref(ref) is True


def test_cumulative_diff_reports_uncommitted_work_outside_the_range(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-dirty")
    worktree = Path(workspace["path"])
    _commit(worktree, "src/story_one.py", "ONE = 1\n")
    (worktree / "src" / "story_two.py").write_text("TWO = 2\n", encoding="utf-8")

    diff = _capture(store, tmp_path, project, workspace, ["devbase"])

    assert diff["state"] == "captured"
    assert diff["nameOnly"] == ["src/story_one.py"]
    assert "TWO = 2" not in diff["patchFull"]
    assert [entry["path"] for entry in diff["status"]] == ["src/story_two.py"]
    assert diff["statusRaw"].strip()


def test_cumulative_diff_over_the_capture_limit_fails_closed_instead_of_a_partial_patch(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-truncated")
    worktree = Path(workspace["path"])
    _commit(worktree, "src/story_one.py", "ONE = 1\n" * 400)
    monkeypatch.setattr(git_worktrees, "CUMULATIVE_DIFF_CAPTURE_LIMIT_BYTES", 1024)

    diff = _capture(store, tmp_path, project, workspace, ["devbase"])

    assert diff["state"] == "capture_truncated"
    assert "patch" in diff["stderr"]
    assert "patchFull" not in diff
