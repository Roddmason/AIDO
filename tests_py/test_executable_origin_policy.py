"""Tests del confinamiento de origen para el ejecutable que la politica deja correr.

El clasificador solo ve un string, y los llamadores reducen ``argv[0]`` a su basename antes de
clasificarlo (`qa_agent._display_command`). El directorio del binario nunca llegaba a la decision,
asi que un binario propio llamado como uno allowlisted heredaba su categoria de bajo riesgo y se
ejecutaba sin aprobacion humana: el nombre del archivo era la unica credencial.

El test que importa no es la lista de orquestadores, es el invariante: **el argv real decide**. Un
ejecutable que no viene del PATH ni del workspace asignado no obtiene `allow`, y los dos origenes
legitimos que el planificador emite —binario del PATH y wrapper versionado del repo— siguen
pasando.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_control_center.agents.qa_agent import _display_command
from local_control_center.security_policy.executable_origin import executable_origin_is_trusted
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.security_policy.sandbox import (
    _resolved_subprocess_argv,
    validate_restricted_process,
)

GATED_OPERATIONS = (
    "qa_agent_command",
    "devops_agent_command",
    "security_agent_scanner",
    "git_workspace_command",
)


def _binary(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text("@echo off\n", encoding="utf-8")
    return target


def _qa_payload(argv: list[str], workspace: Path) -> dict[str, object]:
    """Payload valido de QAAgent: sin la guarda de origen, todos estos casos son ``allow``."""
    return {
        "agentId": "qa_agent",
        "tool": "shell",
        "permissionProfile": "qa",
        "operation": "qa_agent_command",
        "workspaceId": "workspace-1",
        "workspacePath": str(workspace),
        "path": str(workspace),
        "agentRunId": "run-1",
        "execute": True,
        "command": _display_command(argv),
        "commandArgv": argv,
    }


def test_attacker_supplied_wrapper_outside_the_workspace_is_denied(tmp_path: Path) -> None:
    """El vector reportado: argv[0] elegido por el llamador, clasificado por su basename."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attacker = _binary(tmp_path / "attacker", "gradlew.bat")

    decision = evaluate_action(_qa_payload([str(attacker), "test"], workspace))

    assert decision["decision"] == "deny"
    assert "executable_origin_denied" in decision["categories"]


def test_attacker_binary_named_after_an_allowlisted_executable_is_denied(tmp_path: Path) -> None:
    """El vector que de verdad corria: el allowlist del sandbox tambien compara por basename.

    `gradlew.bat` moria en `sandbox.ALLOWED_EXECUTABLES`, pero un binario propio llamado `node.exe`
    pasaba clasificador y sandbox, y `_resolved_subprocess_argv` conservaba la ruta del atacante.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attacker = _binary(tmp_path / "attacker", "node.exe")

    argv = [str(attacker), "--version"]
    assert validate_restricted_process(argv, str(workspace), str(workspace)) is None
    assert _resolved_subprocess_argv(argv)[0] == str(attacker)

    decision = evaluate_action(_qa_payload(argv, workspace))

    assert decision["decision"] == "deny"
    assert "executable_origin_denied" in decision["categories"]


def test_runtime_cli_operations_keep_their_own_executable_contract(tmp_path: Path) -> None:
    """La CLI registrada por el operador no vive en el PATH; su contrato lo valida el runtime."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    configured = _binary(tmp_path / "opt" / "codex", "codex.cmd")

    decision = evaluate_action(
        {
            "tool": "shell",
            "operation": "developer_agent_runtime",
            "workspacePath": str(workspace),
            "path": str(workspace),
            "execute": True,
            "command": "codex exec",
            "commandArgv": [str(configured), "exec"],
        }
    )

    assert "executable_origin_denied" not in decision["categories"]


def test_workspace_wrapper_still_runs(tmp_path: Path) -> None:
    """El wrapper versionado del repo es intencional: es la version que el proyecto fijo."""
    workspace = tmp_path / "workspace"
    wrapper = _binary(workspace, "gradlew.bat")

    decision = evaluate_action(_qa_payload([str(wrapper), "--console=plain", "test"], workspace))

    assert decision["decision"] == "allow"


def test_path_resolved_binary_still_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`shutil.which` entrega rutas absolutas del PATH; el planificador las emite tal cual."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path_dir = tmp_path / "tools"
    resolved = _binary(path_dir, "go.exe")
    monkeypatch.setenv("PATH", str(path_dir))

    decision = evaluate_action(_qa_payload([str(resolved), "test", "./..."], workspace))

    assert decision["decision"] == "allow"


def test_bare_executable_name_still_runs(tmp_path: Path) -> None:
    """Un nombre pelado lo resuelve el PATH al ejecutar; es lo que emiten git y los fallbacks."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_action(_qa_payload(["go", "test", "./..."], workspace))

    assert decision["decision"] == "allow"


def test_running_interpreter_still_runs(tmp_path: Path) -> None:
    """Los pasos de calidad y el dispatcher invocan `sys.executable`, que vive fuera del PATH."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_action(_qa_payload([sys.executable, "--version"], workspace))

    assert decision["decision"] == "allow"


def test_a_sibling_of_the_interpreter_is_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Se confia en el archivo del interprete, no en su directorio: el venv no es un PATH extra.

    El interprete se reubica a un directorio temporal a proposito. Apuntando al venv real, este
    test dependia del entorno: bajo `uv run` el `Scripts/` del venv **si** esta en el PATH, asi
    que el veredicto lo daba la regla del PATH y no la del interprete, y el invariante que este
    test existe para fijar quedaba sin cubrir.
    """
    interpreter_home = tmp_path / "fake-venv" / "Scripts"
    interpreter_home.mkdir(parents=True)
    interpreter = interpreter_home / "python.exe"
    interpreter.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(interpreter))
    sibling = str(interpreter.with_name("node.exe"))

    assert executable_origin_is_trusted([str(interpreter), "--version"], workspace_path=None) is True
    assert executable_origin_is_trusted([sibling, "--version"], workspace_path=None) is False


def test_traversal_out_of_the_workspace_is_denied(tmp_path: Path) -> None:
    """`..` dentro de una ruta del workspace no puede comprar la confianza del workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _binary(tmp_path / "attacker", "gradlew.bat")
    traversal = workspace / ".." / "attacker" / "gradlew.bat"

    decision = evaluate_action(_qa_payload([str(traversal), "test"], workspace))

    assert decision["decision"] == "deny"
    assert "executable_origin_denied" in decision["categories"]


@pytest.mark.parametrize("operation", GATED_OPERATIONS)
def test_every_shell_toolchain_operation_is_gated(operation: str, tmp_path: Path) -> None:
    """La guarda corre antes de las reglas por operacion, asi que ninguna la esquiva."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attacker = _binary(tmp_path / "attacker", "git.exe")

    decision = evaluate_action(
        {
            "tool": "shell",
            "operation": operation,
            "workspacePath": str(workspace),
            "path": str(workspace),
            "execute": True,
            "command": "git status",
            "commandArgv": [str(attacker), "status"],
        }
    )

    assert decision["decision"] == "deny"
    assert "executable_origin_denied" in decision["categories"]


def test_non_executable_evaluation_is_untouched(tmp_path: Path) -> None:
    """Sin ``execute`` no se lanza subproceso, y la guarda no debe inventar denegaciones."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attacker = _binary(tmp_path / "attacker", "gradlew.bat")
    payload = _qa_payload([str(attacker), "test"], workspace)
    payload["execute"] = False

    assert evaluate_action(payload)["decision"] == "allow"


def test_missing_workspace_denies_instead_of_trusting_the_basename(tmp_path: Path) -> None:
    """Sin workspace declarado no hay contencion que probar: la duda cae al lado seguro."""
    attacker = _binary(tmp_path / "attacker", "gradlew.bat")

    assert executable_origin_is_trusted([str(attacker), "test"], workspace_path=None) is False
    assert executable_origin_is_trusted(["gradlew.bat", "test"], workspace_path=None) is True


def test_empty_argv_is_not_trusted() -> None:
    """Un argv vacio o mal formado no puede probar su origen."""
    assert executable_origin_is_trusted([], workspace_path="C:/ws") is False
    assert executable_origin_is_trusted(None, workspace_path="C:/ws") is False
