"""Tests del gate de QA proporcional para historias cuyo cambio es solo documentacion.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.agents.qa_doc_gate import (
    DOC_ONLY_SKIP_REASON,
    is_doc_only_change,
    resolve_doc_only_qa_plan,
)


def test_doc_only_change_true_for_markdown_only() -> None:
    assert is_doc_only_change(["docs/guide.md", "README.md"]) is True


def test_doc_only_change_true_for_mdx_rst_adoc_mix() -> None:
    assert is_doc_only_change(["docs/guide.mdx", "CHANGELOG.rst", "docs/adr/0001.adoc"]) is True


def test_doc_only_change_false_when_python_file_present() -> None:
    assert is_doc_only_change(["docs/guide.md", "local_control_center/agents/qa_agent.py"]) is False


def test_doc_only_change_false_for_requirements_txt() -> None:
    """`.txt` nunca cuenta como documentacion: `requirements*.txt` es un manifiesto de dependencias."""
    assert is_doc_only_change(["requirements.txt"]) is False
    assert is_doc_only_change(["docs/guide.md", "requirements-dev.txt"]) is False


def test_doc_only_change_false_for_empty_changed_paths() -> None:
    """Fail-safe: sin archivos que clasificar, no se afirma que el cambio sea solo documentacion."""
    assert is_doc_only_change([]) is False


def test_doc_only_change_false_for_github_workflow_markdown() -> None:
    """Un `.md` bajo `.github/` es config del repo (plantillas de issue/PR), no documentacion pura."""
    assert is_doc_only_change([".github/ISSUE_TEMPLATE/bug_report.md"]) is False
    assert is_doc_only_change(["docs/guide.md", ".github/PULL_REQUEST_TEMPLATE.md"]) is False


def test_resolve_doc_only_qa_plan_reduces_to_diff_check_and_skips_discovered_commands(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"private":true,"scripts":{"test":"node -e \\"process.exit(0)\\""}}\n',
        encoding="utf-8",
    )

    real_commands, skipped_commands = resolve_doc_only_qa_plan(tmp_path)

    assert len(real_commands) == 1
    assert real_commands[0]["argv"] == ["git", "diff", "--check"]
    assert real_commands[0]["critical"] is True
    assert len(skipped_commands) == 1
    assert skipped_commands[0]["argv"] == ["corepack", "pnpm@10.24.0", "run", "test"]
    assert skipped_commands[0]["reason"] == DOC_ONLY_SKIP_REASON


def test_resolve_doc_only_qa_plan_has_no_skipped_commands_without_manifests(tmp_path: Path) -> None:
    real_commands, skipped_commands = resolve_doc_only_qa_plan(tmp_path)

    assert len(real_commands) == 1
    assert skipped_commands == []
