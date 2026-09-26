"""El DeveloperAgent sobre un modelo puede borrar archivos, con los mismos guardas que al escribir.

Visto en vivo: sin contexto del repo, gemma dejó ``textkit/utils.py`` en la raíz de un proyecto con
layout ``src/``; con solo escritura ese archivo mal ubicado no tenía arreglo en ningún rework.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.developer_agent import _parse_model_patch
from local_control_center.agents.runtime_adapters.workspace_patch import WorkspacePatchBrokerAdapter


def _apply(workspace: Path, patch: dict) -> dict:
    with closing(sqlite3.connect(":memory:")) as connection:
        return WorkspacePatchBrokerAdapter(connection=connection).execute(
            tool_call={"input": patch},
            policy_input={"workspacePath": str(workspace), "projectId": "project-test"},
        )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "textkit").mkdir(parents=True)
    (root / "textkit" / "utils.py").write_bytes(b"def top_words(text):\n    return []\n")
    (root / "README.md").write_bytes(b"# project\n")
    return root


def test_patch_deletes_a_misplaced_file_and_writes_its_replacement(workspace: Path) -> None:
    result = _apply(
        workspace,
        {
            "files": [{"path": "src/textkit/utils.py", "content": "def top_words(text):\n    return []\n"}],
            "deleteFiles": ["textkit/utils.py"],
        },
    )

    assert result["executed"] is True, result
    assert result["deletedFiles"] == ["textkit/utils.py"]
    assert not (workspace / "textkit" / "utils.py").exists()
    assert (workspace / "src" / "textkit" / "utils.py").exists()


def test_patch_with_only_deletions_is_accepted(workspace: Path) -> None:
    result = _apply(workspace, {"files": [], "deleteFiles": ["textkit/utils.py"]})

    assert result["executed"] is True, result
    assert not (workspace / "textkit" / "utils.py").exists()


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("../outside.py", "traversal"),
        (".env", "secret or credential"),
        ("textkit", "not a regular file"),
    ],
)
def test_patch_refuses_unsafe_or_missing_deletions_without_touching_anything(
    workspace: Path, path: str, reason: str
) -> None:
    (workspace / ".env").write_bytes(b"TOKEN=placeholder\n")

    result = _apply(
        workspace,
        {"files": [{"path": "new.py", "content": "x = 1\n"}], "deleteFiles": [path]},
    )

    assert result["blocked"] is True
    assert reason in result["reason"]
    assert not (workspace / "new.py").exists()
    assert (workspace / "textkit" / "utils.py").exists()


def test_patch_refuses_to_delete_a_file_it_also_writes(workspace: Path) -> None:
    result = _apply(
        workspace,
        {
            "files": [{"path": "README.md", "content": "# project\n"}],
            "deleteFiles": ["README.md"],
        },
    )

    assert result["blocked"] is True
    assert "also written" in result["reason"]


def test_model_patch_parser_accepts_deletions_and_rejects_an_empty_patch() -> None:
    parsed = _parse_model_patch(
        '{"summary":"move","files":[],"deleteFiles":["textkit/utils.py"],"tests":[],"risks":[]}'
    )

    assert parsed["deleteFiles"] == ["textkit/utils.py"]
    with pytest.raises(ValueError, match="files or deleteFiles"):
        _parse_model_patch('{"summary":"nothing","files":[],"tests":[],"risks":[]}')
    with pytest.raises(ValueError, match="deleteFiles"):
        _parse_model_patch('{"summary":"bad","files":[],"deleteFiles":[3],"tests":[],"risks":[]}')


def test_deleting_a_file_that_is_already_gone_is_a_no_op(workspace: Path) -> None:
    """Visto en vivo: la historia 1 borró el archivo suelto y la 3 volvió a pedirlo; fallar todo el
    patch por un estado que ya se cumple bloqueó el rework. Se registra y el resto se aplica."""
    (workspace / "textkit" / "utils.py").unlink()

    result = _apply(
        workspace,
        {"files": [{"path": "README.md", "content": "# restored\n"}], "deleteFiles": ["textkit/utils.py"]},
    )

    assert result["executed"] is True, result
    assert result["deletedFiles"] == []
    assert result["alreadyAbsentFiles"] == ["textkit/utils.py"]
    assert (workspace / "README.md").read_text(encoding="utf-8") == "# restored\n"
