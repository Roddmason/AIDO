from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from local_control_center.security_policy.git_command_runner import git_available, run_git


def slugify_branch_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return cleaned[:48] or "task"


def is_git_repository(path: Path) -> bool:
    if not git_available():
        return False
    result = run_git(["-C", str(path), "rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def create_git_worktree(
    *,
    repo_path: Path,
    worktree_path: Path,
    task_id: str,
    workspace_id: str,
    base_branch: str = "HEAD",
) -> dict[str, Any]:
    if not git_available():
        return {"status": "degraded_git_unavailable"}
    if not is_git_repository(repo_path):
        return {"status": "degraded_not_git_repo"}
    branch_name = f"aido/{slugify_branch_segment(task_id)}/{workspace_id.removeprefix('workspace-')[:8]}"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(["-C", str(repo_path), "worktree", "add", "-b", branch_name, str(worktree_path), base_branch])
    if result.returncode != 0:
        return {
            "status": "degraded_worktree_failed",
            "branchName": branch_name,
            "stderr": result.stderr.strip()[:1000],
        }
    return {
        "status": "created",
        "branchName": branch_name,
        "baseBranch": base_branch,
    }
