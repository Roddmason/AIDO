"""Claves de la llamada de modelo local: arranque en frío previsto y salida estructurada ya validada.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.architect_agent import ArchitectAgentRunner
from local_control_center.agents.architect_agent_contract import ARCHITECT_AGENT_ID, architect_agent_contract
from local_control_center.agents.developer_agent import (
    DEVELOPER_MODEL_PATCH_SCHEMA,
    DEVELOPER_MODEL_PATCH_SCHEMA_NAME,
    DeveloperAgentRunner,
)
from local_control_center.agents.local_model_call_input import local_model_call_input
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
from local_control_center.agents.product_owner_agent_contract import (
    PRODUCT_OWNER_AGENT_ID,
    product_owner_agent_contract,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_adapters.models import RuntimeExecutionRequest
from local_control_center.agents.runtime_adapters.provider_factory import ProviderFactoryAdapter
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import running_llama_router

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


def test_the_developer_model_call_requests_structured_output_once_validated(connection, tmp_path: Path):
    LocalModelSettingsRepository(connection).upsert(
        "llama_cpp", "qwen-b", actor="runtime_validation", json_schema=True
    )
    broker = _CapturingBroker()
    DeveloperAgentRunner(connection, root=tmp_path)._execute_model_runtime(
        payload={"projectId": "project-local", "model": "qwen-b", "instruction": "Add a README."},
        runtime={"id": "llama_cpp", "kind": "local", "providerFamily": "openai_compatible"},
        workspace={"id": "workspace-local", "path": str(tmp_path)},
        agent_run={"id": "agent-run-local"},
        job={"id": "job-local"},
        profile={},
        broker=broker,
    )
    call_input = broker.tool_calls[0]["input"]
    assert call_input["structuredOutput"] == "json_schema"
    assert call_input["responseSchema"] == {
        "name": DEVELOPER_MODEL_PATCH_SCHEMA_NAME,
        "schema": DEVELOPER_MODEL_PATCH_SCHEMA,
    }


def test_the_product_owner_model_call_requests_structured_output_once_validated(connection, tmp_path: Path):
    kwargs = {
        "payload": {"projectId": "project-local", "model": "gemma-a"},
        "runtime": {"id": "llama_cpp", "kind": "local", "providerFamily": "openai_compatible"},
        "workspace": {"id": "workspace-local", "path": str(tmp_path)},
        "agent_run": {"id": "agent-run-local"},
        "job": {"id": "job-local"},
        "profile": {},
        "messages": [{"role": "user", "content": "hola"}],
    }
    before = _CapturingBroker()
    ProductOwnerAgentRunner(connection, root=tmp_path)._execute_model_runtime(broker=before, **kwargs)
    assert "structuredOutput" not in before.tool_calls[0]["input"]

    LocalModelSettingsRepository(connection).upsert(
        "llama_cpp", "gemma-a", actor="runtime_validation", json_schema=True
    )
    after = _CapturingBroker()
    ProductOwnerAgentRunner(connection, root=tmp_path)._execute_model_runtime(broker=after, **kwargs)
    call_input = after.tool_calls[0]["input"]
    assert call_input["structuredOutput"] == "json_schema"
    assert call_input["responseSchema"] == {
        "name": PRODUCT_OWNER_AGENT_ID,
        "schema": product_owner_agent_contract()["outputSchema"],
    }


def test_the_architect_model_call_requests_structured_output_once_validated(connection, tmp_path: Path):
    kwargs = {
        "payload": {"projectId": "project-local", "model": "gemma-a", "diffArtifactId": "artifact-1"},
        "runtime": {"id": "llama_cpp", "kind": "local", "providerFamily": "openai_compatible"},
        "workspace": {"id": "workspace-local", "path": str(tmp_path)},
        "agent_run": {"id": "agent-run-local"},
        "job": {"id": "job-local"},
        "profile": {},
        "diff_text": "diff --git a/f b/f\n",
    }
    before = _CapturingBroker()
    ArchitectAgentRunner(connection, root=tmp_path)._execute_model_runtime(broker=before, **kwargs)
    assert "structuredOutput" not in before.tool_calls[0]["input"]

    LocalModelSettingsRepository(connection).upsert(
        "llama_cpp", "gemma-a", actor="runtime_validation", json_schema=True
    )
    after = _CapturingBroker()
    ArchitectAgentRunner(connection, root=tmp_path)._execute_model_runtime(broker=after, **kwargs)
    call_input = after.tool_calls[0]["input"]
    assert call_input["structuredOutput"] == "json_schema"
    assert call_input["responseSchema"] == {
        "name": ARCHITECT_AGENT_ID,
        "schema": architect_agent_contract()["outputSchema"],
    }


def test_structured_output_reaches_the_provider_as_a_strict_json_schema_response_format(
    connection, tmp_path: Path
):
    LocalModelSettingsRepository(connection).upsert(
        "llama_cpp", "gemma-a", actor="runtime_validation", json_schema=True
    )
    with running_llama_router() as (root, router):
        ProviderAccountStore(connection).patch_provider_account("llama_cpp", {"baseUrl": f"{root}/v1"})
        result = ProviderFactoryAdapter(
            provider_family="openai_compatible", display_name="llama.cpp", connection=connection
        ).execute(
            RuntimeExecutionRequest(
                projectId="project-local",
                workspaceId="workspace-local",
                workspacePath=str(tmp_path),
                capability="chat",
                input={
                    "providerId": "llama_cpp",
                    "model": "gemma-a",
                    "messages": [{"role": "user", "content": "hi"}],
                    **local_model_call_input(
                        connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA
                    ),
                },
            )
        )
    assert result.status == "completed"
    assert router.chat_bodies[-1]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA["name"], "schema": SCHEMA["schema"], "strict": True},
    }


def _record_probe(monkeypatch: pytest.MonkeyPatch, *, result: bool | Exception) -> list[dict]:
    """Reemplaza la sonda real de `runtime_team.probe` y registra con qué se la llamó."""
    from local_control_center.runtime_team import probe

    calls: list[dict] = []

    def fake_probe(provider, model_id, profile, *, was_loaded):
        calls.append({"model": model_id, "was_loaded": was_loaded})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(probe, "probe_local_json_schema_capability", fake_probe)
    return calls


def test_the_first_json_contract_call_discovers_the_capability_of_an_unprobed_model(connection, monkeypatch):
    """Visto en vivo: el PO corrió en un modelo nunca sondeado y sin gramática omitió un campo requerido."""
    calls = _record_probe(monkeypatch, result=True)

    first = local_model_call_input(
        connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA
    )
    second = local_model_call_input(
        connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA
    )

    assert first == second == {"structuredOutput": "json_schema", "responseSchema": SCHEMA}
    assert calls == [{"model": "gemma-a", "was_loaded": True}]
    setting = LocalModelSettingsRepository(connection).get("llama_cpp", "gemma-a")
    assert setting.json_schema is True
    assert setting.provenance["json_schema"] == "runtime_validation"


def test_an_unloaded_model_is_probed_with_the_cold_start_budget(connection, monkeypatch):
    calls = _record_probe(monkeypatch, result=False)

    result = local_model_call_input(
        connection, provider_id="llama_cpp", model="qwen-b", response_schema=SCHEMA
    )

    assert calls == [{"model": "qwen-b", "was_loaded": False}]
    assert result == {"coldStartExpected": True}


def test_a_known_capability_is_never_probed_again(connection, monkeypatch):
    LocalModelSettingsRepository(connection).upsert(
        "llama_cpp", "gemma-a", actor="operator", json_schema=False
    )
    calls = _record_probe(monkeypatch, result=True)

    assert (
        local_model_call_input(connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA)
        == {}
    )
    assert calls == []


def test_a_failing_probe_leaves_the_call_without_structured_output(connection, monkeypatch):
    _record_probe(monkeypatch, result=OSError("connection refused"))

    assert (
        local_model_call_input(connection, provider_id="llama_cpp", model="gemma-a", response_schema=SCHEMA)
        == {}
    )
    setting = LocalModelSettingsRepository(connection).get("llama_cpp", "gemma-a")
    assert setting is None or "json_schema" not in setting.provenance
