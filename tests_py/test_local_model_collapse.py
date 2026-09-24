"""Una cuenta local aporta un solo candidato por rol: el modelo resuelto; el resto queda rechazado.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import urllib.request
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import running_llama_router


class _States:
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states

    def get(self, account, *, max_wait_s: float = 1.0) -> dict[str, str]:
        return dict(self.states)


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    statuses = [
        {
            "id": "llama_cpp",
            "kind": "local",
            "configured": True,
            "available": True,
            "executable": True,
            "capabilities": ["chat"],
            "reason": "Controlled local endpoint.",
            # La selección por Jev exige evidencia de salud vigente antes de mirar la validación del modelo.
            "healthStatus": "healthy",
            "healthCheckedAt": datetime.now(UTC).isoformat(),
        }
    ]
    monkeypatch.setattr(
        RuntimeStatusService, "list_provider_statuses", lambda _service, *, project_id=None: statuses
    )
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        ResourceRepository(handle).record_sample(ResourceSnapshot.test_snapshot())
        handle.execute("UPDATE model_catalog SET enabled = 0")
        store = ProviderAccountStore(handle)
        store.upsert_provider_account(
            {
                "providerId": "llama_cpp",
                "displayName": "llama.cpp",
                "providerType": "local",
                "apiFormat": "openai_compatible",
                "providerFamily": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        store.set_provider_catalog_id("llama_cpp", "llama_cpp")
        for model in ("gemma-a", "qwen-b"):
            store.upsert_model(
                {
                    "providerId": "llama_cpp",
                    "model": model,
                    "enabled": True,
                    "freeTier": True,
                    "inputPricePerMtok": 0,
                    "outputPricePerMtok": 0,
                    "source": "test",
                }
            )
        yield handle


def _states(monkeypatch: pytest.MonkeyPatch, states: dict[str, str]) -> None:
    monkeypatch.setattr(local_model_state, "LOAD_STATE_CACHE", _States(states))


def _select(connection, **overrides):
    request = AIResourceRequest(
        task_type="product_owner.discovery",
        required_capabilities=overrides.pop("required_capabilities", ["chat"]),
        allowed_provider_ids=["llama_cpp"],
        **overrides,
    )
    return AIResourceManager(connection).select_resource(request, record=False)


def _reasons(decision) -> dict[str, str]:
    return {
        item["model"]: item["reason"] for item in decision["rejected"] if item["providerId"] == "llama_cpp"
    }


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def test_one_local_account_gives_a_single_candidate_the_loaded_model(connection, monkeypatch):
    _states(monkeypatch, {"gemma-a": "unloaded", "qwen-b": "loaded"})
    decision = _select(connection)
    assert [item["model"] for item in decision["candidates"] if item["providerId"] == "llama_cpp"] == [
        "qwen-b"
    ]
    assert _reasons(decision) == {"gemma-a": "local_model_not_selected"}
    assert decision["policyResult"]["localModelSelections"] == [
        {
            "runtimeId": "llama_cpp",
            "model": "qwen-b",
            "reason": "loaded",
            "requiresSwitch": False,
            "fromModel": None,
        }
    ]


def test_unknown_load_state_uses_the_default_without_a_switch(connection, monkeypatch):
    _states(monkeypatch, {})
    LocalModelSettingsRepository(connection).upsert("llama_cpp", "qwen-b", actor="operator", is_default=True)
    decision = _select(connection)
    assert decision["selected"]["model"] == "qwen-b"
    assert decision["policyResult"]["localModelSelections"][0]["requiresSwitch"] is False


def test_a_role_capability_forces_a_switch_away_from_the_loaded_model(connection, monkeypatch):
    _states(monkeypatch, {"gemma-a": "loaded", "qwen-b": "unloaded"})
    LocalModelSettingsRepository(connection).upsert("llama_cpp", "qwen-b", actor="operator", code_edit=True)
    decision = _select(connection, required_capabilities=["chat", "code"])
    assert decision["selected"]["model"] == "qwen-b"
    assert _reasons(decision) == {"gemma-a": "missing_capabilities:code"}
    assert decision["policyResult"]["localModelSelections"] == [
        {
            "runtimeId": "llama_cpp",
            "model": "qwen-b",
            "reason": "operator_order",
            "requiresSwitch": True,
            "fromModel": "gemma-a",
        }
    ]


def test_affinity_from_another_role_of_the_run_is_preferred(connection, monkeypatch):
    _states(monkeypatch, {})
    LocalModelSettingsRepository(connection).upsert("llama_cpp", "gemma-a", actor="operator", is_default=True)
    decision = _select(connection, local_model_affinity={"llama_cpp": "qwen-b"})
    assert decision["selected"]["model"] == "qwen-b"
    assert decision["policyResult"]["localModelSelections"][0]["reason"] == "affinity"


def test_a_sealed_model_wins_over_the_loaded_one(connection, monkeypatch):
    _states(monkeypatch, {"gemma-a": "unloaded", "qwen-b": "loaded"})
    decision = _select(connection, local_model_pins={"llama_cpp": "gemma-a"})
    assert decision["selected"]["model"] == "gemma-a"
    assert _reasons(decision) == {"qwen-b": "local_model_not_selected"}
    assert decision["policyResult"]["localModelSelections"] == [
        {
            "runtimeId": "llama_cpp",
            "model": "gemma-a",
            "reason": "sealed",
            "requiresSwitch": True,
            "fromModel": "qwen-b",
        }
    ]


def test_a_model_sealed_under_a_router_alias_resolves_to_its_canonical_model(connection, monkeypatch):
    _states(monkeypatch, {"gemma-a": "loaded", "qwen-b": "unloaded"})
    monkeypatch.setattr(local_model_state, "model_aliases_for", lambda _account: {"local": "gemma-a"})
    decision = _select(connection, local_model_pins={"llama_cpp": "local"})
    assert decision["selected"]["model"] == "gemma-a"
    assert _reasons(decision) == {"qwen-b": "local_model_not_selected"}
    assert decision["policyResult"]["localModelSelections"] == [
        {
            "runtimeId": "llama_cpp",
            "model": "gemma-a",
            "reason": "sealed",
            "requiresSwitch": False,
            "fromModel": None,
        }
    ]


def test_runtime_selection_prefers_validated_models_and_blocks_when_all_failed(connection, monkeypatch):
    _states(monkeypatch, {"gemma-a": "unloaded", "qwen-b": "loaded"})
    SettingsRepository(connection).set_value("decision_engine.mode", "general", None, "runtime_selection")
    record_model_execution(connection, "llama_cpp", "gemma-a", True, "test_prompt", started_at=_ago(3))
    decision = _select(connection)
    # Una vista previa (record=False) no consulta a Jev: la evidencia es el único candidato que Jev vería.
    assert [item["model"] for item in decision["candidates"] if item["providerId"] == "llama_cpp"] == [
        "gemma-a"
    ]
    engine = decision["policyResult"].get("decisionEngine") or {}
    assert engine.get("reasonCode") != "confidence_below_threshold"
    assert _reasons(decision)["qwen-b"] == "local_model_not_selected"
    assert decision["policyResult"]["localModelSelections"][0]["model"] == "gemma-a"
    record_model_execution(connection, "llama_cpp", "gemma-a", False, "test_prompt", started_at=_ago(2))
    record_model_execution(connection, "llama_cpp", "qwen-b", False, "test_prompt", started_at=_ago(1))
    blocked = _select(connection)
    assert _reasons(blocked) == {
        "gemma-a": "local_model_not_validated",
        "qwen-b": "local_model_not_validated",
    }
    assert blocked["policyResult"]["localModelSelections"] == []


def _chat(root: str, model: str) -> None:
    body = {"model": model, "messages": [{"role": "user", "content": "Reply OK."}]}
    request = urllib.request.Request(
        f"{root}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200


def test_the_router_autoload_switch_is_seen_by_the_selection_after_the_cache_ttl(connection, monkeypatch):
    ticks = [0.0]
    cache = local_model_state.LoadStateCache(clock=lambda: ticks[0])
    monkeypatch.setattr(local_model_state, "LOAD_STATE_CACHE", cache)
    pins = {"llama_cpp": "gpt-oss-20b"}
    with running_llama_router(autoload_delay_s=0.05) as (root, router):
        store = ProviderAccountStore(connection)
        store.patch_provider_account("llama_cpp", {"baseUrl": f"{root}/v1"})
        for model in ("gemma-4-26b-a4b", "gpt-oss-20b"):
            store.upsert_model(
                {
                    "providerId": "llama_cpp",
                    "model": model,
                    "enabled": True,
                    "freeTier": True,
                    "inputPricePerMtok": 0,
                    "outputPricePerMtok": 0,
                    "source": "test",
                }
            )
        account = store.get_provider_account("llama_cpp")
        assert cache.get(account, max_wait_s=5.0)["gemma-4-26b-a4b"] == "loaded"
        before = _select(connection, local_model_pins=pins)
        _chat(root, "gpt-oss-20b")
        within_ttl = cache.get(account, max_wait_s=5.0)
        ticks[0] += local_model_state.LOAD_STATE_TTL_SECONDS + 1
        assert cache.get(account, max_wait_s=5.0)["gpt-oss-20b"] == "loaded"
        after = _select(connection, local_model_pins=pins)
    assert before["policyResult"]["localModelSelections"] == [
        {
            "runtimeId": "llama_cpp",
            "model": "gpt-oss-20b",
            "reason": "sealed",
            "requiresSwitch": True,
            "fromModel": "gemma-4-26b-a4b",
        }
    ]
    assert within_ttl["gpt-oss-20b"] == "unloaded"
    assert after["policyResult"]["localModelSelections"] == [
        {
            "runtimeId": "llama_cpp",
            "model": "gpt-oss-20b",
            "reason": "sealed",
            "requiresSwitch": False,
            "fromModel": None,
        }
    ]
    assert [item["status"]["value"] for item in router.models] == ["unloaded", "loaded", "unloaded"]
