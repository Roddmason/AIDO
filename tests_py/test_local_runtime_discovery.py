"""Detección de runtimes locales: solo 127.0.0.1, GET sin credenciales, firmas y lecturas acotadas.

Los puertos siempre son los de dobles efímeros: ningún test sondea los puertos fijos del host.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

import pytest

from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.executions.registration import register_operation
from local_control_center.executions.workloads import operation_workload
from local_control_center.local_runtimes import discovery
from local_control_center.local_runtimes.discovery import discover_local_runtimes
from tests_py.fakes.local_llm_servers import (
    LLAMA_ROUTER_MODELS,
    generic_openai_routes,
    json_route_server,
    lm_studio_routes,
    running_llama_router,
    vllm_routes,
)
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store

ALLOWED_PATHS = {"/v1/models", "/props", "/api/v1/models", "/api/v0/models"}


def test_discovery_identifies_each_server_signature_without_credentials() -> None:
    with ExitStack() as stack:
        llama_root, llama_state = stack.enter_context(running_llama_router())
        llama_port = urlparse(llama_root).port
        lms = stack.enter_context(json_route_server(lm_studio_routes(models=["qwen3-8b"], loaded=set())))
        vllm = stack.enter_context(json_route_server(vllm_routes(model="Qwen/Qwen3-8B")))
        generic = stack.enter_context(json_route_server(generic_openai_routes(models=["local-model"])))
        suggestions = discover_local_runtimes(
            configured_base_urls=[f"http://localhost:{lms.port}/v1", "https://api.openai.com/v1"],
            ports=[llama_port, lms.port, vllm.port, generic.port],
        )
        llama_requests = [
            {"method": method, "path": path, "authorization": authorization}
            for method, path, authorization in llama_state.requests
        ]
        requests = [*llama_requests, *lms.requests, *vllm.requests, *generic.requests]
    by_server = {item["server"]: item for item in suggestions}
    assert by_server["llama_cpp"] == {
        "catalogId": "llama_cpp",
        "baseUrl": f"http://127.0.0.1:{llama_port}/v1",
        "server": "llama_cpp",
        "models": [model["id"] for model in LLAMA_ROUTER_MODELS],
        "alreadyConfigured": False,
        "requiresConfirmation": False,
    }
    assert (by_server["lm_studio"]["catalogId"], by_server["lm_studio"]["alreadyConfigured"]) == (
        "lm_studio",
        True,
    )
    assert (by_server["vllm"]["catalogId"], by_server["vllm"]["models"]) == ("vllm", ["Qwen/Qwen3-8B"])
    unknown = by_server["unknown_openai_compatible"]
    assert (unknown["catalogId"], unknown["requiresConfirmation"]) == ("local_openai_compatible", True)
    assert all(request["method"] == "GET" for request in requests)
    assert all(request["authorization"] is None for request in requests)
    assert {request["path"] for request in requests} <= ALLOWED_PATHS


def test_discovery_bounds_model_lists_and_ignores_oversized_bodies() -> None:
    oversized = b'{"data": [' + b'{"id": "x"},' * 30000 + b'{"id": "y"}]}'
    with (
        json_route_server(generic_openai_routes(models=[f"model-{index}" for index in range(500)])) as many,
        json_route_server({("GET", "/v1/models"): (200, oversized)}) as huge,
    ):
        suggestions = discover_local_runtimes(configured_base_urls=[], ports=[many.port, huge.port])
    assert len(suggestions) == 1
    assert suggestions[0]["baseUrl"] == f"http://127.0.0.1:{many.port}/v1"
    assert len(suggestions[0]["models"]) == 64
    assert suggestions[0]["models"][0] == "model-0"


def test_discover_route_requires_the_write_token_and_never_creates_accounts(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with json_route_server(vllm_routes(model="Qwen/Qwen3-8B")) as server:
        monkeypatch.setattr(discovery, "DISCOVERY_PORTS", (server.port,))
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        before = store.connection.execute("SELECT COUNT(*) FROM provider_accounts").fetchone()[0]
        anonymous = client.post("/api/v1/local-runtimes/discover")
        response = client.post("/api/v1/local-runtimes/discover", headers=headers)
        after = store.connection.execute("SELECT COUNT(*) FROM provider_accounts").fetchone()[0]
    assert anonymous.status_code == 403
    assert response.status_code == 200, response.text
    assert response.json()["suggestions"] == [
        {
            "catalogId": "vllm",
            "baseUrl": f"http://127.0.0.1:{server.port}/v1",
            "server": "vllm",
            "models": ["Qwen/Qwen3-8B"],
            "alreadyConfigured": False,
            "requiresConfirmation": False,
        }
    ]
    assert before == after


def test_discover_operation_registers_for_the_dispatcher_as_control_plane_work(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "isolated.sqlite")
    try:
        runtime.init()
        register_operation(runtime, "local_runtimes.discover")
        spec, handler = runtime.execution_handlers["local_runtimes.discover"]
        workload = operation_workload(runtime.connection, spec, {})
    finally:
        runtime.close()
    assert (spec.workload_class, workload) == ("control_plane", "control_plane")
    assert handler.__name__ == "discover_runtimes"
