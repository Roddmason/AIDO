"""Salida inválida del modelo del DeveloperAgent: un intento de reparación y, si no, falla (no "no ejecutable").

Visto en vivo: gemma entró en un bucle de repetición en un rework y cortó el JSON por tope de tokens
(46 KB de la misma línea). El runner lo clasificaba como ``runtime_unavailable`` y el loop mostraba
"DeveloperAgent runtime llama_cpp is not executable", con acciones de revalidar un runtime sano.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

VALID_PATCH = json.dumps(
    {"summary": "fix", "files": [{"path": "notes.md", "content": "ok\n"}], "tests": [], "risks": []}
)
DEGENERATE = '{"summary": "fix", "files": [{"path": "notes.md", "content": "' + "# again\\n" * 400


class _ScriptedBroker:
    """Contesta la llamada al modelo como completada y aplica el patch; registra cada llamada."""

    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []

    def evaluate_tool_call(self, **kwargs: Any) -> dict[str, Any]:
        tool_call = kwargs["tool_call"]
        self.tool_calls.append(tool_call)
        return {
            "toolCall": {
                "id": f"agent-tool-call-{len(self.tool_calls)}",
                "status": "completed",
                "payload": {"execution": "test", "executionResult": {"returnCode": 0}},
            }
        }


def _run(tmp_path: Path, outputs: list[str]) -> tuple[dict[str, Any], _ScriptedBroker]:
    broker = _ScriptedBroker()
    with closing(open_sqlite_connection(tmp_path / "developer.sqlite")) as connection:
        initialize_platform_schema(connection)
        runner = DeveloperAgentRunner(connection, root=tmp_path)
        pending = list(outputs)
        runner._model_output_text = lambda _result: pending.pop(0)  # type: ignore[method-assign]
        result = runner._execute_model_runtime(
            payload={"projectId": "project-local", "model": "gemma", "instruction": "Fix notes."},
            runtime={"id": "llama_cpp", "kind": "local", "providerFamily": "openai_compatible"},
            workspace={"id": "workspace-local", "path": str(tmp_path)},
            agent_run={"id": "agent-run-local"},
            job={"id": "job-local"},
            profile={},
            broker=broker,
        )
    return result, broker


def _model_calls(broker: _ScriptedBroker) -> list[dict[str, Any]]:
    return [call for call in broker.tool_calls if call["operation"] == "developer_agent_model_call"]


def test_a_degenerate_output_gets_one_repair_call_that_explains_the_failure(tmp_path: Path) -> None:
    result, broker = _run(tmp_path, [DEGENERATE, VALID_PATCH])

    calls = _model_calls(broker)
    assert len(calls) == 2
    repair_note = calls[1]["input"]["messages"][-1]["content"]
    assert "not valid JSON" in repair_note
    assert result["status"] == "completed"
    assert result["repairAttempts"] == 1


def test_a_second_invalid_output_fails_as_invalid_output_not_as_an_unavailable_runtime(
    tmp_path: Path,
) -> None:
    result, broker = _run(tmp_path, [DEGENERATE, DEGENERATE])

    assert len(_model_calls(broker)) == 2
    assert result["status"] == "failed"
    assert "not valid JSON" in result["reason"]
    assert result["repairAttempts"] == 1


@pytest.mark.parametrize("first", [VALID_PATCH])
def test_a_valid_first_output_needs_no_repair(tmp_path: Path, first: str) -> None:
    result, broker = _run(tmp_path, [first])

    assert len(_model_calls(broker)) == 1
    assert result["status"] == "completed"
    assert result.get("repairAttempts", 0) == 0
