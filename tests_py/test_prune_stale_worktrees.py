"""Verifica que la poda de worktrees de automatización solo remueve lo probadamente seguro.

Construye un repo git real con worktrees en cada estado (fusionado y limpio, con commit único,
con cambios reales, con rama de prefijo ajeno, y recién modificado) y asegura que ``select_candidates``
solo marca el fusionado-limpio-y-antiguo, que el resto se conserva con motivo, y que ``prune`` en
dry-run no toca nada mientras que con ``apply`` remueve el worktree y su rama.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prune-stale-worktrees.py"
_spec = importlib.util.spec_from_file_location("prune_stale_worktrees", _SCRIPT)
assert _spec and _spec.loader
psw = importlib.util.module_from_spec(_spec)
# Registrar en sys.modules antes de ejecutar: @dataclass resuelve tipos vía sys.modules[__module__].
sys.modules[_spec.name] = psw
_spec.loader.exec_module(psw)

_TWO_HOURS_AGO_OFFSET = 7200


def _git(repo: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd or repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-c", "init.defaultBranch=dev", "init", str(repo)], capture_output=True, check=True
    )
    _git(repo, "config", "user.email", "t@t.cl")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "file.txt").write_text("linea base\n", encoding="utf-8", newline="\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _add_worktree(repo: Path, name: str, branch: str) -> Path:
    path = repo / ".tmp" / "workspaces" / name
    _git(repo, "worktree", "add", "-b", branch, str(path), "dev")
    return path


def _age(path: Path, seconds_ago: int) -> None:
    stamp = time.time() - seconds_ago
    os.utime(path, (stamp, stamp))


def test_select_candidates_only_marks_merged_clean_and_aged(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    now = time.time()

    merged = _add_worktree(repo, "workspace-merged", "aido/run-merged")
    unmerged = _add_worktree(repo, "workspace-unmerged", "aido/run-unmerged")
    (unmerged / "new.txt").write_text("trabajo\n", encoding="utf-8", newline="\n")
    _git(repo, "add", ".", cwd=unmerged)
    _git(repo, "commit", "-m", "commit unico", cwd=unmerged)
    dirty = _add_worktree(repo, "workspace-dirty", "aido/run-dirty")
    (dirty / "file.txt").write_text("linea base\ncambio real sin commit\n", encoding="utf-8", newline="\n")
    foreign = _add_worktree(repo, "workspace-foreign", "feature/other")
    fresh = _add_worktree(repo, "workspace-fresh", "aido/run-fresh")

    for path in (merged, unmerged, dirty, foreign):
        _age(path, _TWO_HOURS_AGO_OFFSET)
    _age(fresh, 0)

    removable, skipped, _ = psw.select_candidates(
        repo, base="dev", branch_prefix="aido/", min_age_minutes=60, now=now
    )

    removable_paths = {psw._normalize(wt.path) for wt in removable}
    assert removable_paths == {psw._normalize(str(merged))}

    skipped_by_path = {psw._normalize(path): reason for path, reason in skipped}
    assert "NO fusionada" in skipped_by_path[psw._normalize(str(unmerged))]
    assert "cambios reales" in skipped_by_path[psw._normalize(str(dirty))]
    assert "prefijo" in skipped_by_path[psw._normalize(str(foreign))]
    assert "umbral" in skipped_by_path[psw._normalize(str(fresh))]


def test_dry_run_removes_nothing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    merged = _add_worktree(repo, "workspace-merged", "aido/run-merged")
    _age(merged, _TWO_HOURS_AGO_OFFSET)

    report = psw.prune(
        repo, base="dev", branch_prefix="aido/", min_age_minutes=60, apply=False, now=time.time()
    )

    assert report.removed == [str(merged)] or psw._normalize(report.removed[0]) == psw._normalize(str(merged))
    assert merged.exists()  # dry-run: sigue en disco
    assert "aido/run-merged" in _git(repo, "branch").stdout


def test_apply_removes_merged_worktree_and_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    merged = _add_worktree(repo, "workspace-merged", "aido/run-merged")
    kept = _add_worktree(repo, "workspace-unmerged", "aido/run-unmerged")
    (kept / "new.txt").write_text("trabajo\n", encoding="utf-8", newline="\n")
    _git(repo, "add", ".", cwd=kept)
    _git(repo, "commit", "-m", "unico", cwd=kept)
    _age(merged, _TWO_HOURS_AGO_OFFSET)
    _age(kept, _TWO_HOURS_AGO_OFFSET)

    report = psw.prune(
        repo, base="dev", branch_prefix="aido/", min_age_minutes=60, apply=True, now=time.time()
    )

    assert not report.failed
    assert not merged.exists()  # removido del disco
    assert kept.exists()  # el no-fusionado se conserva
    branches = _git(repo, "branch").stdout
    assert "aido/run-merged" not in branches
    assert "aido/run-unmerged" in branches
