"""Claves de la llamada de modelo local: arranque en frío previsto y salida estructurada ya validada.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.local_model_call_input import local_model_call_input
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

SCHEMA = {
    "name": "aido_patch",
    "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
}


class _States:
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states

    def get(self, account, *, max_wait_s: float = 1.0) -> dict[str, str]:
        return dict(self.states)


class _CapturingBroker:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def evaluate_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs["tool_call"])
        return {"toolCall": {"id": "agent-tool-call-test", "status": "failed", "payload": {}}}


def _account(provider_id: str, base_url: str) -> dict:
    return {
        "providerId": provider_id,
        "displayName": provider_id,
        "providerType": "local",
        "apiFormat": "openai_compatible",
        "providerFamily": "openai_compatible",
        "baseUrl": base_url,
        "enabled": True,
    }


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        local_model_state, "LOAD_STATE_CACHE", _States({"gemma-a": "loaded", "qwen-b": "unloaded"})
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        store = ProviderAccountStore(handle)
        store.upsert_provider_account(_account("llama_cpp", "http://127.0.0.1:1/v1"))
        store.set_provider_catalog_id("llama_cpp", "llama_cpp")
        store.upsert_provider_account(_account("public-endpoint", "https://models.example.invalid/v1"))
        for model in ("gemma-a", "qwen-b"):
            store.upsert_model({"providerId": "llama_cpp", "model": model, "enabled": True, "source": "test"})
        yield handle


def test_only_a_model_the_server_reports_as_not_loaded_expects_a_cold_start(connection):
    assert local_model_call_input(connection, provider_id="llama_cpp", model="qwen-b") == {
        "coldStartExpected": True
    }
    assert local_model_call_input(connection, provider_id="llama_cpp", model="gemma-a") == {}
    assert local_model_call_input(connection, provider_id="llama_cpp", model="never-listed") == {}


def test_structured_output_is_requested_only_after_json_schema_was_validated(connection):
    assert (
        local_model_call_input(connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA)
        == {}
    )
    LocalModelSettingsRepository(connection).upsert(
        "llama_cpp", "gemma-a", actor="runtime_validation", json_schema=True
    )
    assert local_model_call_input(
        connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA
    ) == {
        "structuredOutput": "json_schema",
        "responseSchema": SCHEMA,
    }
    assert local_model_call_input(connection, provider_id="llama_cpp", model="gemma-a") == {}


def test_remote_missing_or_modelless_accounts_get_no_local_keys(connection):
    LocalModelSettingsRepository(connection).upsert(
        "public-endpoint", "qwen-b", actor="runtime_validation", json_schema=True
    )
    assert (
        local_model_call_input(
            connection, provider_id="public-endpoint", model="qwen-b", response_schema=SCHEMA
        )
        == {}
    )
    assert local_model_call_input(connection, provider_id="missing", model="qwen-b") == {}
    assert local_model_call_input(connection, provider_id="llama_cpp", model=None) == {}


def test_the_developer_model_call_carries_the_local_keys(connection, tmp_path: Path):
    broker = _CapturingBroker()
    result = DeveloperAgentRunner(connection, root=tmp_path)._execute_model_runtime(
        payload={"projectId": "project-local", "model": "qwen-b", "instruction": "Add a README."},
        runtime={"id": "llama_cpp", "kind": "local", "providerFamily": "openai_compatible"},
        workspace={"id": "workspace-local", "path": str(tmp_path)},
        agent_run={"id": "agent-run-local"},
        job={"id": "job-local"},
        profile={},
        broker=broker,
    )
    assert result["status"] == "failed"
    call_input = broker.tool_calls[0]["input"]
    assert call_input["coldStartExpected"] is True
    assert "structuredOutput" not in call_input
    assert "extraBody" not in call_input
