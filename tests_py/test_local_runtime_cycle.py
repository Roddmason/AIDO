"""Ciclo completo de LM Studio, vLLM y servidor genérico: salud, sync, validación real y candidato del equipo.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_control_center.local_runtimes import endpoints
from local_control_center.runtime_team.probe import RuntimeValidationService
from tests_py.fakes.local_llm_servers import (
    generic_openai_routes,
    json_route_server,
    lm_studio_routes,
    vllm_routes,
)
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

CHAT_ROUTE = ("POST", "/v1/chat/completions")


def _json_ok_chat(record: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Chat OpenAI-compatible que responde el JSON de validación ``{"ok": true}``."""
    model = json.loads(record["body"] or "{}").get("model") or "unknown"
    return 200, {
        "id": "chatcmpl-cycle",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


@pytest.mark.parametrize(
    ("catalog_id", "routes", "model", "loaded"),
    [
        pytest.param(
            "lm_studio",
            lm_studio_routes(models=["qwen3-8b"], loaded={"qwen3-8b"}),
            "qwen3-8b",
            ["qwen3-8b"],
            id="lm_studio",
        ),
        pytest.param(
            "vllm", vllm_routes(model="Qwen/Qwen3-8B"), "Qwen/Qwen3-8B", ["Qwen/Qwen3-8B"], id="vllm"
        ),
        pytest.param(
            "local_openai_compatible",
            generic_openai_routes(models=["local-model"]),
            "local-model",
            [],
            id="generic",
        ),
    ],
)
def test_a_local_runtime_completes_the_same_cycle_as_llama_cpp(
    client_with_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_id: str,
    routes: dict[tuple[str, str], Any],
    model: str,
    loaded: list[str],
) -> None:
    monkeypatch.setattr(endpoints, "VIEW_LOAD_STATE_WAIT_S", 5.0)
    provider_id = f"cycle-{catalog_id.replace('_', '-')}"
    with json_route_server({**routes, CHAT_ROUTE: _json_ok_chat}) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/local-endpoints",
            headers=headers,
            json={"catalogId": catalog_id, "instanceId": provider_id, "baseUrl": server.base_url},
        )
        assert created.status_code == 201, created.text
        health = client.post(f"/api/v1/model-gateway/providers/{provider_id}/health-check", headers=headers)
        synced = client.post(f"/api/v1/provider-accounts/{provider_id}/sync-models", headers=headers)
        validation = RuntimeValidationService(store.connection).validate(provider_id, model=model)
        team = client.get("/api/v1/runtime/team-candidates")
        chat_calls = [item for item in server.requests if (item["method"], item["path"]) == CHAT_ROUTE]
    assert health.status_code == 200, health.text
    assert health.json()["health"]["healthStatus"] == "healthy"
    assert synced.status_code == 200, synced.text
    assert [item["model"] for item in synced.json()["models"]] == [model]
    assert (validation["status"], validation["model"]) == ("validated", model)
    assert [json.loads(item["body"])["model"] for item in chat_calls] == [model]
    assert team.status_code == 200, team.text
    candidate = next(item for item in team.json()["candidates"] if item["providerId"] == provider_id)
    assert candidate["kind"] == "local"
    assert candidate["validation"]["status"] == "validated"
    assert candidate["loadedModels"] == loaded


def test_a_vllm_server_whose_health_answers_but_lists_no_models_is_not_healthy(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_models = (200, {"object": "list", "data": []})
    routes = {**vllm_routes(model="Qwen/Qwen3-8B"), ("GET", "/v1/models"): empty_models}
    with json_route_server(routes) as server:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/local-endpoints",
            headers=headers,
            json={"catalogId": "vllm", "instanceId": "cycle-vllm-empty", "baseUrl": server.base_url},
        )
        assert created.status_code == 201, created.text
        health = client.post("/api/v1/model-gateway/providers/cycle-vllm-empty/health-check", headers=headers)
    body = health.json()["health"]
    assert body["healthStatus"] != "healthy"
    assert body["message"].startswith("local_server_unreachable")
