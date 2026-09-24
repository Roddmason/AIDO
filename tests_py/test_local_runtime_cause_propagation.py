"""La causa local del adapter viaja en el resultado de PO, Developer y Architect (P9)."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents import architect_agent, developer_agent, product_owner_agent
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

AGENT_MODULES = [product_owner_agent, developer_agent, architect_agent]
AGENT_IDS = ["product-owner", "developer", "architect"]


def failed_model_call(failure_cause: str | None) -> dict:
    return {
        "id": "agent-tool-call-local",
        "status": "unavailable",
        "payload": {
            "execution": "runtime_adapter:openai_compatible",
            "executionResult": {
                "status": "unavailable",
                "blocked": True,
                "reason": f"OpenAI-compatible execution failed: {failure_cause}",
                "failureCause": failure_cause,
            },
        },
    }


@pytest.mark.parametrize("module", AGENT_MODULES, ids=AGENT_IDS)
def test_model_call_failure_projects_the_local_runtime_cause(module) -> None:
    result = module._execution_result_from_tool_call(failed_model_call("model_loading"))

    assert result["status"] == "failed"
    assert result["localRuntimeCause"] == "model_loading"


@pytest.mark.parametrize("module", AGENT_MODULES, ids=AGENT_IDS)
def test_non_local_failures_report_no_local_runtime_cause(module) -> None:
    result = module._execution_result_from_tool_call(failed_model_call("provider_request_failed"))

    assert result["localRuntimeCause"] is None


def test_product_owner_keeps_the_cli_failure_cause_separate_from_the_local_cause() -> None:
    result = product_owner_agent._execution_result_from_tool_call(failed_model_call("model_loading"))

    assert result["failureCause"] is None
    assert result["localRuntimeCause"] == "model_loading"


def test_developer_model_failure_carries_the_local_runtime_cause(tmp_path: Path, monkeypatch) -> None:
    class Broker:
        def evaluate_tool_call(self, **_kwargs):
            return {"toolCall": failed_model_call("local_server_unreachable")}

    monkeypatch.setattr(
        developer_agent,
        "_developer_model_messages",
        lambda **_kwargs: [{"role": "user", "content": "Create a file."}],
    )
    with closing(open_sqlite_connection(tmp_path / "developer.sqlite")) as connection:
        initialize_platform_schema(connection)
        result = developer_agent.DeveloperAgentRunner(connection, root=tmp_path)._execute_model_runtime(
            payload={"projectId": "project-local", "instruction": "Create a file.", "model": "qwen3-coder"},
            runtime={
                "id": "llama-local",
                "providerFamily": "openai_compatible",
                "kind": "local",
                "models": ["qwen3-coder"],
            },
            workspace={"id": "workspace-local", "path": str(tmp_path)},
            agent_run={"id": "agent-run-local"},
            job={"id": "job-local"},
            profile={},
            broker=Broker(),
        )

    assert result["status"] == "failed"
    assert result["localRuntimeCause"] == "local_server_unreachable"
