"""Los comandos del proyecto no heredan el virtualenv con el que corre el propio AIDO.

Visto en vivo: el QA de un proyecto uv avisaba ``VIRTUAL_ENV=...\\AIDO\\.venv does not match the
project environment`` porque AIDO corre desde su ``.venv`` (``uv run``) con ``Scripts`` primero en
el ``PATH``; un ``python``/``pytest`` sin ``uv run`` resolvía al intérprete de AIDO.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from local_control_center.security_policy import sandbox
from local_control_center.security_policy.sandbox import (
    _resolved_subprocess_argv,
    project_command_environment,
)


@pytest.fixture
def aido_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    venv = tmp_path / "aido-venv"
    (venv / "Scripts").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base-python"))
    return venv


def test_project_environment_drops_the_aido_virtualenv(aido_venv: Path, tmp_path: Path) -> None:
    other = tmp_path / "tools"
    other.mkdir()
    base = {
        "VIRTUAL_ENV": str(aido_venv),
        "PATH": os.pathsep.join([str(aido_venv / "Scripts"), str(other)]),
        "UV_RUN_RECURSION_DEPTH": "1",
        "UV_CACHE_DIR": "H:/cache/uv",
    }

    environment = project_command_environment(base)

    assert "VIRTUAL_ENV" not in environment
    assert "UV_RUN_RECURSION_DEPTH" not in environment
    assert environment["PATH"] == str(other)
    assert environment["UV_CACHE_DIR"] == "H:/cache/uv"


def test_project_environment_keeps_a_foreign_virtualenv(aido_venv: Path, tmp_path: Path) -> None:
    foreign = tmp_path / "project" / ".venv"
    base = {"VIRTUAL_ENV": str(foreign), "PATH": str(foreign / "Scripts")}

    environment = project_command_environment(base)

    assert environment == base


def test_project_environment_is_untouched_outside_a_virtualenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    base = {"VIRTUAL_ENV": sys.prefix, "PATH": str(Path(sys.prefix) / "Scripts")}

    assert project_command_environment(base) == base


def test_project_commands_resolve_executables_outside_the_aido_virtualenv(
    aido_venv: Path, tmp_path: Path
) -> None:
    other = tmp_path / "tools"
    other.mkdir()
    name = "pytest.exe" if os.name == "nt" else "pytest"
    for folder in (aido_venv / "Scripts", other):
        executable = folder / name
        executable.write_bytes(b"")
        executable.chmod(0o755)

    resolved = _resolved_subprocess_argv(["pytest", "-q"], search_path=str(other))

    assert Path(resolved[0]).parent == other
    assert resolved[1:] == ["-q"]


def test_restricted_execute_runs_project_commands_with_the_project_environment(
    aido_venv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_capture(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["environment"]
        return {"returnCode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(sandbox, "run_supervised_capture", fake_capture)
    monkeypatch.setenv("VIRTUAL_ENV", str(aido_venv))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = sandbox.RestrictedSubprocessSandbox().execute(
        argv=["git", "status"], cwd=str(workspace), workspace_path=str(workspace)
    )

    assert result["executed"] is True
    assert "VIRTUAL_ENV" not in captured["environment"]


def test_restricted_execute_keeps_an_explicit_trusted_environment(
    aido_venv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        sandbox,
        "run_supervised_capture",
        lambda command, **kwargs: captured.update(environment=kwargs["environment"]) or {"returnCode": 0},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trusted = {"VIRTUAL_ENV": str(aido_venv), "PATH": ""}

    sandbox.RestrictedSubprocessSandbox().execute(
        argv=["git", "status"], cwd=str(workspace), workspace_path=str(workspace), environment=trusted
    )

    assert captured["environment"] is trusted
