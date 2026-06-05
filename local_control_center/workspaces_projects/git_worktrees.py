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


def git_head_commit(path: Path, ref: str = "HEAD") -> str | None:
    if not git_available() or not is_git_repository(path):
        return None
    result = run_git(["-C", str(path), "rev-parse", ref])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_current_branch(path: Path) -> str | None:
    if not git_available() or not is_git_repository(path):
        return None
    result = run_git(["-C", str(path), "branch", "--show-current"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


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
    source_commit = git_head_commit(repo_path, base_branch)
    source_branch = git_current_branch(repo_path)
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
        "sourceCommit": source_commit,
        "sourceBranch": source_branch,
    }


def remove_git_worktree(*, repo_path: Path, worktree_path: Path) -> dict[str, Any]:
    if not git_available():
        return {"status": "cleanup_failed_git_unavailable"}
    result = run_git(["-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)])
    if result.returncode != 0:
        return {"status": "cleanup_failed", "stderr": result.stderr.strip()[:1000]}
    return {"status": "removed"}


def _parse_porcelain_status(output: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2].strip() or line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            previous, current = path.split(" -> ", 1)
            items.append({"status": status, "path": current, "previousPath": previous})
        elif path:
            items.append({"status": status, "path": path})
    return items


def capture_git_diff(workspace_path: Path) -> dict[str, Any]:
    if not git_available():
        return {"kind": "git_diff", "state": "degraded_git_unavailable", "statusRaw": "", "status": []}
    if not is_git_repository(workspace_path):
        return {"kind": "git_diff", "state": "degraded_not_git_repo", "statusRaw": "", "status": []}

    status_result = run_git(["-C", str(workspace_path), "status", "--porcelain=v1"])
    run_git(["-C", str(workspace_path), "add", "--intent-to-add", "--", "."])
    branch_result = run_git(["-C", str(workspace_path), "branch", "--show-current"])
    head_result = run_git(["-C", str(workspace_path), "rev-parse", "HEAD"])
    name_result = run_git(["-C", str(workspace_path), "diff", "--name-only"])
    stat_result = run_git(["-C", str(workspace_path), "diff", "--stat", "--", "."])
    patch_result = run_git(["-C", str(workspace_path), "diff", "--", "."])
    if status_result.returncode != 0:
        return {
            "kind": "git_diff",
            "state": "capture_failed",
            "stderr": status_result.stderr.strip()[:2000],
            "files": [],
        }

    changed = _parse_porcelain_status(status_result.stdout)
    name_only = [line.strip() for line in name_result.stdout.splitlines() if line.strip()]
    for item in changed:
        if item["path"] not in name_only:
            name_only.append(item["path"])

    patch = patch_result.stdout if patch_result.returncode == 0 else ""
    return {
        "kind": "git_diff",
        "state": "captured",
        "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else None,
        "headCommit": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "statusRaw": status_result.stdout,
        "status": changed,
        "nameOnly": name_only,
        "diffStat": stat_result.stdout[:4000] if stat_result.returncode == 0 else "",
        "patch": patch[:12000],
        "patchFull": patch,
        "patchSizeBytes": len(patch.encode("utf-8")),
        "truncated": len(patch) > 12000,
    }
