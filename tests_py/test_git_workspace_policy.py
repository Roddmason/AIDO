"""Regresión de la política de comandos git del workspace: las dos formas de `git add`.

El slice de commit (8a53ef26) agregó la rama `add -A` ENCIMA de la rama `--intent-to-add`
preexistente dentro de la misma función; como la primera retorna en todos sus caminos, la
segunda quedó muerta y todo capture de diff con archivos nuevos empezó a producir un patch
vacío. Estos tests fijan que ambas formas convivan y que el resto siga denegado.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.security_policy.policy_engine import evaluate_action


def _git_payload(argv: list[str], git_operation: str) -> dict[str, Any]:
    return {
        "agentId": "git_workspace_agent",
        "tool": "shell",
        "operation": "git_workspace_command",
        "permissionProfile": "dev_safe",
        "workspaceId": "workspace-1",
        "workspacePath": "C:/ws",
        "path": "C:/ws",
        "agentRunId": "run-1",
        "commandArgv": argv,
        "gitOperation": git_operation,
        "networkRequired": False,
        "secretsRequired": False,
    }


def test_intent_to_add_for_diff_capture_is_allowed() -> None:
    decision = evaluate_action(
        _git_payload(["git", "add", "--intent-to-add", "--", "."], "diff_capture_intent_to_add")
    )

    assert decision["decision"] == "allow", decision["reason"]
    assert "git_intent_to_add" in decision["categories"]


def test_stage_all_for_commit_is_allowed() -> None:
    decision = evaluate_action(_git_payload(["git", "add", "-A"], "stage_changes"))

    assert decision["decision"] == "allow", decision["reason"]
    assert "git_add" in decision["categories"]


def test_any_other_add_shape_is_denied() -> None:
    for argv, operation in (
        (["git", "add", "."], "stage_changes"),
        (["git", "add", "-A"], "diff_capture_intent_to_add"),
        (["git", "add", "--intent-to-add", "--", "."], "stage_changes"),
        (["git", "add", "--intent-to-add", "--", "src"], "diff_capture_intent_to_add"),
    ):
        decision = evaluate_action(_git_payload(argv, operation))
        assert decision["decision"] == "deny", f"{argv} {operation} debió denegarse"
