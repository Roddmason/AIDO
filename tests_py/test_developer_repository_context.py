"""Contexto del repositorio para el DeveloperAgent sobre un modelo sin herramientas.

Reproduce lo visto en vivo: sin mapa, gemma creó ``textkit/utils.py`` en la raíz de un proyecto con
layout ``src/textkit/`` (QA: ``ModuleNotFoundError``) y reescribió el README sin conocerlo.

@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_control_center.agents import developer_repository_context as context_module
from local_control_center.agents.developer_agent import _developer_model_messages
from local_control_center.agents.developer_repository_context import (
    MAX_CONTENT_CHARS,
    MAX_FILE_CHARS,
    render_repository_context,
    repository_context,
)
from local_control_center.security_policy.git_command_runner import git_available

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is required")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


@pytest.fixture
def src_layout_repo(tmp_path: Path) -> Path:
    root = tmp_path / "textkit-project"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root, ".gitignore", "local-only.txt\n")
    _write(root, "README.md", "# textkit\n\nUtilities for text.\n")
    _write(root, "pyproject.toml", '[project]\nname = "textkit"\n')
    _write(root, "src/textkit/__init__.py", '"""textkit."""\n')
    _write(root, "src/textkit/stats.py", "def word_count(text):\n    return len(text.split())\n")
    _write(root, "tests/test_stats.py", "from textkit.stats import word_count\n")
    _write(root, "uv.lock", "version = 1\n")
    _write(root, ".env", "API_URL=http://example.invalid\n")
    _write(root, "config/credentials.json", '{"user": "demo"}\n')
    _write(root, "src/textkit/credentials.py", "def load():\n    return None\n")
    _write(root, "local-only.txt", "ignored by git\n")
    _git(root, "add", "-A")
    return root


def test_context_lists_the_real_layout_and_the_current_content(src_layout_repo: Path) -> None:
    rendered = repository_context(src_layout_repo, focus_text="Add top_words to textkit stats")

    assert "- src/textkit/stats.py" in rendered
    assert "- tests/test_stats.py" in rendered
    assert "=== README.md ===\n# textkit\n\nUtilities for text.\n" in rendered
    assert "def word_count(text):" in rendered
    assert "never create a parallel top-level package" in rendered


def test_context_excludes_secret_containers_ignored_files_and_lockfile_content(src_layout_repo: Path) -> None:
    rendered = repository_context(src_layout_repo, focus_text="anything")

    assert "- .env" not in rendered
    assert "API_URL" not in rendered
    assert "config/credentials.json" not in rendered
    assert "- local-only.txt" not in rendered
    assert "- src/textkit/credentials.py" in rendered
    assert "- uv.lock" in rendered
    assert "=== uv.lock ===" not in rendered


def test_context_prioritizes_what_the_story_mentions(src_layout_repo: Path) -> None:
    rendered = repository_context(src_layout_repo, focus_text="Extend stats with a new helper")

    sections = [line for line in rendered.splitlines() if line.startswith("=== ")]
    assert sections[0] == "=== src/textkit/stats.py ==="


def test_context_redacts_secrets_in_included_content(tmp_path: Path) -> None:
    root = tmp_path / "redaction"
    root.mkdir()
    token = "sk-" + "developercontext123456"
    _write(root, "app.py", f'CLIENT = "{token}"\n')

    rendered = render_repository_context(root, ["app.py"], focus_text="")

    assert "=== app.py ===" in rendered
    assert token not in rendered


def test_context_respects_the_budget_and_names_what_it_omits(tmp_path: Path) -> None:
    root = tmp_path / "budget"
    root.mkdir()
    _write(root, "huge.py", "x" * (MAX_FILE_CHARS + 1))
    paths = ["huge.py"]
    for index in range(12):
        name = f"mod_{index:02d}.py"
        _write(root, name, "y" * (MAX_FILE_CHARS - 100))
        paths.append(name)

    rendered = render_repository_context(root, paths, focus_text="")

    included = sum(1 for line in rendered.splitlines() if line.startswith("=== "))
    assert included * (MAX_FILE_CHARS - 100) <= MAX_CONTENT_CHARS
    assert "=== huge.py ===" not in rendered
    assert "huge.py" in rendered.split("Existing files whose content is not shown")[1]


def test_context_is_empty_outside_a_git_repository(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "plain folder\n")

    assert repository_context(tmp_path, focus_text="") == ""


def test_developer_model_messages_carry_the_repository_context(src_layout_repo: Path) -> None:
    messages = _developer_model_messages(
        instruction="Add top_words to textkit.",
        qa_commands=[],
        workspace_path=str(src_layout_repo),
    )
    without_workspace = _developer_model_messages(instruction="Add top_words to textkit.", qa_commands=[])

    assert "- src/textkit/stats.py" in messages[1]["content"]
    assert "Repository files" not in without_workspace[1]["content"]


def test_git_failure_degrades_to_no_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module,
        "run_git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr=""
        ),
    )

    assert context_module.workspace_paths(tmp_path) == []
