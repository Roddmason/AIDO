"""Poda segura de worktrees de automatización acumulados bajo ``.tmp/workspaces``.

La automatización de AIDO aísla cada corrida en un git worktree y solo lo elimina cuando el
workspace se archiva (``WorkspacesRepository.archive_workspace`` -> ``remove_git_worktree``).
Las corridas interrumpidas o no archivadas dejan el worktree colgado, y con el tiempo se
acumulan cientos (observado: 807), degradando el rendimiento de git y ocupando disco.

Este mantenimiento reclama ese espacio SIN tocar el motor en vivo ni datos de evidencia. Es
seguro por diseño y conservador: solo considera worktrees bajo ``.tmp/workspaces`` cuya rama
lleve el prefijo de automatización, y solo poda cuando TODAS estas condiciones se cumplen:

* la rama está completamente fusionada en la base (``git merge-base --is-ancestor``), de modo
  que no hay commits únicos que perder;
* el worktree no tiene cambios reales sin commitear (se ignora ruido de whitespace/CRLF, pero
  cualquier archivo nuevo untracked o edición real lo excluye: podría ser trabajo en curso);
* el worktree no fue modificado dentro de la ventana de antigüedad mínima, para nunca tocar
  una corrida potencialmente activa.

Corre en DRY-RUN por defecto: lista lo que podaría y por qué se salta cada worktree. Requiere
``--apply`` explícito para remover. Idempotente: re-ejecutarlo no hace daño.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_BRANCH = "dev"
DEFAULT_BRANCH_PREFIX = "aido/"
DEFAULT_MIN_AGE_MINUTES = 60
WORKSPACES_MARKER = ".tmp/workspaces"
GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Worktree:
    """Un worktree registrado: su ruta, la rama que tiene checked-out y si git lo marca prunable."""

    path: str
    branch: str | None
    is_prunable: bool


@dataclass
class PruneReport:
    """Resultado de un pase de poda: candidatos removidos y worktrees salteados con su motivo."""

    removed: list[str] = field(default_factory=list)
    pruned_admin: list[str] = field(default_factory=list)
    branches_deleted: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _run_git(repo: Path, args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Ejecuta ``git`` (en ``cwd`` o en ``repo``) capturando texto; nunca lanza por returncode."""
    location = cwd if cwd is not None else repo
    return subprocess.run(
        ["git", "-C", str(location), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def list_worktrees(repo: Path) -> list[Worktree]:
    """Parsea ``git worktree list --porcelain`` a registros ``Worktree`` (vacío si git falla)."""
    result = _run_git(repo, ["worktree", "list", "--porcelain"])
    if result.returncode != 0:
        return []
    worktrees: list[Worktree] = []
    path: str | None = None
    branch: str | None = None
    prunable = False
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
            branch = None
            prunable = False
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            branch = ref.removeprefix("refs/heads/")
        elif line.startswith("prunable"):
            prunable = True
        elif line == "" and path is not None:
            worktrees.append(Worktree(path=path, branch=branch, is_prunable=prunable))
            path = None
    if path is not None:
        worktrees.append(Worktree(path=path, branch=branch, is_prunable=prunable))
    return worktrees


def is_branch_merged(repo: Path, branch: str, base: str) -> bool:
    """True si ``branch`` es ancestro de ``base`` (fusionada, sin commits únicos que perder)."""
    result = _run_git(repo, ["merge-base", "--is-ancestor", f"refs/heads/{branch}", base])
    return result.returncode == 0


def has_real_uncommitted_changes(worktree_path: Path) -> bool:
    """True si el worktree tiene archivos nuevos untracked o ediciones reales (ignora whitespace/CRLF)."""
    if not worktree_path.exists():
        return False
    untracked = _run_git(worktree_path, ["ls-files", "--others", "--exclude-standard"], cwd=worktree_path)
    if untracked.returncode == 0 and untracked.stdout.strip():
        return True
    for scope in (
        ["diff", "--ignore-all-space", "--name-only"],
        ["diff", "--cached", "--ignore-all-space", "--name-only"],
    ):
        diff = _run_git(worktree_path, scope, cwd=worktree_path)
        if diff.returncode == 0 and diff.stdout.strip():
            return True
    return False


def _age_minutes(worktree_path: Path, now: float) -> float:
    try:
        return (now - worktree_path.stat().st_mtime) / 60.0
    except OSError:
        return float("inf")


def select_candidates(
    repo: Path,
    *,
    base: str,
    branch_prefix: str,
    min_age_minutes: float,
    now: float,
) -> tuple[list[Worktree], list[tuple[str, str]], list[Worktree]]:
    """Clasifica los worktrees de ``.tmp/workspaces`` en (removibles, salteados+motivo, prunables-admin).

    ``prunables-admin`` son entradas cuyo directorio ya no existe (git las marca ``prunable``): su
    admin se limpia con ``git worktree prune`` sin pérdida. Un worktree solo es removible cuando su
    rama lleva ``branch_prefix``, está fusionada en ``base``, no tiene cambios reales y superó la
    ventana de antigüedad mínima.
    """
    removable: list[Worktree] = []
    skipped: list[tuple[str, str]] = []
    prunable_admin: list[Worktree] = []
    for wt in list_worktrees(repo):
        if WORKSPACES_MARKER not in _normalize(wt.path):
            continue
        if wt.is_prunable or not Path(wt.path).exists():
            prunable_admin.append(wt)
            continue
        if not wt.branch or not wt.branch.startswith(branch_prefix):
            skipped.append((wt.path, f"branch no coincide con prefijo '{branch_prefix}': {wt.branch}"))
            continue
        if not is_branch_merged(repo, wt.branch, base):
            skipped.append((wt.path, f"rama NO fusionada en {base} (tiene commits únicos)"))
            continue
        if has_real_uncommitted_changes(Path(wt.path)):
            skipped.append((wt.path, "tiene cambios reales sin commitear (posible corrida activa)"))
            continue
        age = _age_minutes(Path(wt.path), now)
        if age < min_age_minutes:
            skipped.append((wt.path, f"modificado hace {age:.0f} min (< umbral {min_age_minutes:.0f})"))
            continue
        removable.append(wt)
    return removable, skipped, prunable_admin


def prune(
    repo: Path, *, base: str, branch_prefix: str, min_age_minutes: float, apply: bool, now: float
) -> PruneReport:
    """Ejecuta (o simula, si ``apply`` es False) la poda y devuelve el reporte con removidos/salteados."""
    removable, skipped, prunable_admin = select_candidates(
        repo, base=base, branch_prefix=branch_prefix, min_age_minutes=min_age_minutes, now=now
    )
    report = PruneReport(skipped=skipped)
    if not apply:
        report.removed = [wt.path for wt in removable]
        report.pruned_admin = [wt.path for wt in prunable_admin]
        report.branches_deleted = [wt.branch for wt in removable if wt.branch]
        return report
    if prunable_admin:
        _run_git(repo, ["worktree", "prune"])
        report.pruned_admin = [wt.path for wt in prunable_admin]
    for wt in removable:
        result = _run_git(repo, ["worktree", "remove", "--force", wt.path])
        if result.returncode != 0:
            report.failed.append((wt.path, result.stderr.strip()[:300]))
            continue
        report.removed.append(wt.path)
        if wt.branch:
            branch_result = _run_git(repo, ["branch", "-D", wt.branch])
            if branch_result.returncode == 0:
                report.branches_deleted.append(wt.branch)
    _run_git(repo, ["worktree", "prune"])
    return report


def _print_report(report: PruneReport, *, apply: bool) -> None:
    verb = "Removidos" if apply else "Se removerían"
    print(f"{verb}: {len(report.removed)} worktree(s)")
    for path in report.removed:
        print(f"  - {path}")
    if report.pruned_admin:
        print(f"Admin prunable ({'limpiado' if apply else 'se limpiaría'}): {len(report.pruned_admin)}")
    if report.branches_deleted:
        print(f"Ramas {'borradas' if apply else 'a borrar'}: {len(report.branches_deleted)}")
    if report.skipped:
        print(f"Salteados (conservados): {len(report.skipped)}")
        for path, reason in report.skipped:
            print(f"  ~ {path}: {reason}")
    if report.failed:
        print(f"FALLARON: {len(report.failed)}")
        for path, reason in report.failed:
            print(f"  ! {path}: {reason}")
    if not apply:
        print("\n(DRY-RUN: nada fue removido. Repite con --apply para ejecutar.)")


def _force_utf8_stdout() -> None:
    """Fuerza stdout/stderr a UTF-8 para que las tildes del reporte no rompan en consolas cp1252."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI: parsea flags, corre la poda y retorna 0 salvo fallo de remoción."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description="Poda segura de worktrees de automatización acumulados.")
    parser.add_argument("--repo", default=".", help="Ruta del repositorio (default: dir actual).")
    parser.add_argument(
        "--base", default=DEFAULT_BASE_BRANCH, help=f"Rama base (default: {DEFAULT_BASE_BRANCH})."
    )
    parser.add_argument(
        "--branch-prefix",
        default=DEFAULT_BRANCH_PREFIX,
        help=f"Prefijo de ramas de automatización a considerar (default: {DEFAULT_BRANCH_PREFIX}).",
    )
    parser.add_argument(
        "--min-age-minutes",
        type=float,
        default=DEFAULT_MIN_AGE_MINUTES,
        help=f"No tocar worktrees modificados hace menos de N min (default: {DEFAULT_MIN_AGE_MINUTES}).",
    )
    parser.add_argument("--apply", action="store_true", help="Ejecutar la remoción (default: dry-run).")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    report = prune(
        repo,
        base=args.base,
        branch_prefix=args.branch_prefix,
        min_age_minutes=args.min_age_minutes,
        apply=args.apply,
        now=time.time(),
    )
    _print_report(report, apply=args.apply)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
