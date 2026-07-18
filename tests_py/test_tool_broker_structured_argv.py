"""Fija el contrato de argv estructurado del ToolBroker: ejecutar exige argv, nunca command.

Regresion de seguridad: `_structured_argv` en modo ejecutable jamas debe caer al parseo del
`command` string (origen potencial LLM); ese fallback existe solo para display/auditoria.
`_executable_argv_boundary` debe negar toda llamada ejecutable sin argv estructurado.

@author Rodrigo Mason
"""

from local_control_center.agents.tool_broker import _executable_argv_boundary, _structured_argv


def test_structured_argv_executable_without_argv_returns_empty() -> None:
    assert _structured_argv({"execute": True}, "rm -rf /", executable=True) == []
    assert _structured_argv({"argv": []}, "git push --force", executable=True) == []
    assert _structured_argv({"argv": ["", "status"]}, "git status", executable=True) == []


def test_structured_argv_display_fallback_parses_command() -> None:
    assert _structured_argv({}, "git status") == ["git", "status"]
    assert _structured_argv({}, "   ") == []
    assert _structured_argv({"argv": ["git", "diff"]}, "ignored") == ["git", "diff"]


def test_executable_argv_boundary_denies_command_only_calls() -> None:
    decision = _executable_argv_boundary({"execute": True}, tool_name="shell", command="rm -rf /")
    assert decision is not None
    assert decision["decision"] == "deny"
    assert decision["categories"] == ["structured_argv_required"]

    allowed = _executable_argv_boundary(
        {"execute": True, "argv": ["git", "status"]}, tool_name="shell", command="git status"
    )
    assert allowed is None
