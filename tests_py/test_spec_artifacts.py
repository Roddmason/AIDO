"""Renders spec-driven al worktree: filtro del diff, gate de no-op, escritura y gitignore.

Ancla los tres guardrails del slice 5 (docs/superpowers/specs/2026-07-28 §5.2): los renders
``.aido/`` jamás cuentan como trabajo real (ni en ``changedFiles`` ni en el gate del Technical
Lead), la escritura ocurre solo dentro del worktree con directorio estable por hilo, y el gitignore
por defecto versiona ``memory/``/``specs/`` mientras sigue ignorando el resto de ``.aido/``.

@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_control_center.git_workspace.service import DEFAULT_GITIGNORE_LINES
from local_control_center.product_loop.coordinator import _changed_files_from_diff, _runtime_changed_files
from local_control_center.product_loop.delivery import technical_lead_gate
from local_control_center.product_loop.spec_artifacts import (
    GENERATED_HEADER,
    is_aido_artifact_path,
    spec_dir_slug,
    write_spec_artifacts,
)


def test_aido_artifact_paths_are_excluded_from_changed_files() -> None:
    assert is_aido_artifact_path(".aido/specs/x/spec.md")
    assert is_aido_artifact_path(".aido\\memory\\constitution.md")
    assert not is_aido_artifact_path("src/.aido-lookalike/file.py")

    diff = {"nameOnly": [".aido/specs/a/spec.md", "src/app.py", ".aido/memory/constitution.md"]}
    assert _changed_files_from_diff(diff) == ["src/app.py"]

    runtime_result = {"diffSummary": {"changedFiles": [".aido/specs/a/tasks.md"]}}
    assert _runtime_changed_files(runtime_result) == []


def test_technical_lead_gate_rejects_a_run_with_only_spec_renders() -> None:
    """Un run cuyo único cambio son los renders .aido/ no es trabajo real: no aterriza."""
    loop = {
        "context": {
            "durableRun": {
                "review": {"changedFiles": [".aido/specs/a/spec.md", ".aido/memory/constitution.md"]},
                "runtimeResult": {"evidencePackage": {"qaVerdict": "passed"}},
            }
        }
    }
    verdict = technical_lead_gate(loop)
    assert verdict["approve"] is False
    assert any("no changed files" in reason for reason in verdict["reasons"])

    loop["context"]["durableRun"]["review"]["changedFiles"].append("src/real_change.py")
    assert technical_lead_gate(loop)["approve"] is True


def test_write_spec_artifacts_renders_generated_markdown_in_stable_dir(tmp_path: Path) -> None:
    result = write_spec_artifacts(
        str(tmp_path),
        thread_id="thread-1",
        title="Checkout flow",
        constitution={
            "title": "Reglas",
            "version": 2,
            "contentHash": "abc123",
            "principles": ["Cambios quirúrgicos"],
            "nonNegotiables": ["Sin secretos"],
        },
        brief={"summary": "Un checkout simple.", "goals": ["Reducir fricción"]},
        stories=[
            {
                "id": "s1",
                "title": "Pagar con tarjeta",
                "asA": "cliente",
                "iWant": "pagar",
                "soThat": "termino rápido",
            }
        ],
        acceptance_criteria=[{"storyId": "s1", "sequence": 1, "criterion": "El pago se confirma"}],
        tasks=[{"id": "t1", "role": "backend_engineer", "title": "API de pago"}],
        task_dependencies=[],
    )

    expected_dir = f".aido/specs/{spec_dir_slug('thread-1', 'Checkout flow')}"
    assert result["specDir"] == expected_dir
    assert set(result["files"]) == {
        ".aido/memory/constitution.md",
        f"{expected_dir}/spec.md",
        f"{expected_dir}/tasks.md",
    }
    constitution_md = (tmp_path / ".aido" / "memory" / "constitution.md").read_text(encoding="utf-8")
    assert constitution_md.startswith(GENERATED_HEADER)
    assert "content-hash: abc123" in constitution_md
    spec_md = (tmp_path / expected_dir / "spec.md").read_text(encoding="utf-8")
    assert "Pagar con tarjeta" in spec_md
    assert "- [ ] El pago se confirma" in spec_md
    tasks_md = (tmp_path / expected_dir / "tasks.md").read_text(encoding="utf-8")
    assert "**backend_engineer**" in tasks_md

    # Mismo hilo => mismo directorio (ampliación, no proliferación); otro hilo => otro directorio.
    assert spec_dir_slug("thread-1", "Checkout flow") == spec_dir_slug("thread-1", "Checkout flow")
    assert spec_dir_slug("thread-2", "Checkout flow") != spec_dir_slug("thread-1", "Checkout flow")


def test_default_gitignore_versions_spec_renders_but_ignores_scratch(tmp_path: Path) -> None:
    """`.aido/memory/` y `.aido/specs/` viajan en git; el resto de `.aido/` sigue ignorado."""
    assert ".aido/*" in DEFAULT_GITIGNORE_LINES
    assert "!.aido/memory/" in DEFAULT_GITIGNORE_LINES
    assert "!.aido/specs/" in DEFAULT_GITIGNORE_LINES

    git = ["git", "-C", str(tmp_path)]
    try:
        subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    (tmp_path / ".gitignore").write_text("\n".join(DEFAULT_GITIGNORE_LINES) + "\n", encoding="utf-8")
    for rel in (".aido/memory/constitution.md", ".aido/specs/x-1/spec.md", ".aido/cache.tmp"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    tracked = subprocess.run(
        [*git, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, check=True
    ).stdout
    assert ".aido/memory/constitution.md" in tracked
    assert ".aido/specs/x-1/spec.md" in tracked
    assert ".aido/cache.tmp" not in tracked
