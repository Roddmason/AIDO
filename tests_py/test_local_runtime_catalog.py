"""Catálogo de runtimes locales: LM Studio, vLLM y servidor genérico con perfil, y lector de LM Studio.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.endpoint_locality import is_self_hosted_inference
from local_control_center.agents.local_model_state import read_lm_studio_rest_states, read_load_states
from local_control_center.agents.provider_catalog import (
    PROVIDER_CATALOG,
    LocalRuntimeProfile,
    provider_catalog_entry,
)
from tests_py.fakes.local_llm_servers import json_route_server, lm_studio_routes
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store


def _get_json(url: str, headers: Mapping[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _lm_studio_account(base_url: str) -> dict[str, Any]:
    return {
        "providerId": "lms-state",
        "providerType": "local",
        "apiFormat": "openai_compatible",
        "providerFamily": "openai_compatible",
        "baseUrl": base_url,
        "credentialRef": "",
        "enabled": True,
        "metadata": {},
        "providerCatalogId": "lm_studio",
        "localDeclaration": None,
        "localConcurrencyLimit": None,
    }


def _lm_studio_profile() -> LocalRuntimeProfile:
    entry = provider_catalog_entry("lm_studio")
    assert entry is not None and entry.local_profile is not None
    return entry.local_profile


def test_local_runtime_entries_precede_the_generic_openai_compatible_entry() -> None:
    ids = [entry.id for entry in PROVIDER_CATALOG]
    generic_index = ids.index("openai_compatible")
    for catalog_id in ("llama_cpp", "lm_studio", "vllm", "local_openai_compatible"):
        assert ids.index(catalog_id) < generic_index
    family = [entry.id for entry in PROVIDER_CATALOG if entry.provider_family == "openai_compatible"]
    assert family[-1] == "openai_compatible"


@pytest.mark.parametrize(
    ("catalog_id", "default_base_url", "profile"),
    [
        (
            "lm_studio",
            "http://127.0.0.1:1234/v1",
            LocalRuntimeProfile(
                liveness_path="/v1/models",
                health_requires_models=True,
                model_state_source="lm_studio_rest",
                multi_model="jit",
                cold_start_timeout_s=180,
            ),
        ),
        (
            "vllm",
            "http://127.0.0.1:8000/v1",
            LocalRuntimeProfile(
                liveness_path="/health",
                health_requires_models=True,
                model_state_source="single_model",
                multi_model="single",
                cold_start_timeout_s=60,
            ),
        ),
        (
            "local_openai_compatible",
            None,
            LocalRuntimeProfile(
                liveness_path="/v1/models",
                health_requires_models=True,
                model_state_source="none",
                multi_model="unknown",
                cold_start_timeout_s=180,
            ),
        ),
    ],
)
def test_local_runtime_presets_declare_their_profile(
    catalog_id: str, default_base_url: str | None, profile: LocalRuntimeProfile
) -> None:
    entry = provider_catalog_entry(catalog_id)
    assert entry is not None
    assert entry.provider_type == "local"
    assert entry.api_format == "openai_compatible"
    assert entry.provider_family == "openai_compatible"
    assert entry.credential_kind == "optional_bearer_token"
    assert entry.pricing_source == "local_runtime_cost_only"
    assert entry.default_base_url == default_base_url
    assert entry.local_profile == profile


@pytest.mark.parametrize("catalog_id", ["lm_studio", "vllm", "local_openai_compatible"])
@pytest.mark.parametrize(
    ("base_url", "exempt"),
    [
        ("http://127.0.0.1:1234/v1", True),
        ("http://192.168.1.20:8000/v1", True),
        ("https://llm.example.com/v1", False),
    ],
    ids=["loopback", "private-literal", "public-name"],
)
def test_a_public_url_under_a_local_preset_is_never_free(
    catalog_id: str, base_url: str, exempt: bool
) -> None:
    account = {
        "providerId": "x",
        "providerCatalogId": catalog_id,
        "providerType": "local",
        "apiFormat": "openai_compatible",
        "baseUrl": base_url,
    }
    assert is_self_hosted_inference(account) is exempt


def test_generic_local_server_requires_an_explicit_base_url(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers, _store = client_with_store(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={"providerId": "local_openai_compatible", "enabled": True},
    )
    assert response.status_code == 422, response.text
    assert "baseUrl is required" in response.json()["detail"]


def test_lm_studio_state_uses_loaded_instances_from_the_v1_rest_api() -> None:
    routes = lm_studio_routes(
        models=["qwen3-8b", "gemma-3-4b"], loaded={"qwen3-8b"}, embeddings=["nomic-embed"]
    )
    with json_route_server(routes) as server:
        states = read_load_states(
            _lm_studio_account(server.base_url), _lm_studio_profile(), http_get_json=_get_json
        )
        paths = [item["path"] for item in server.requests]
    assert states == {"qwen3-8b": "loaded", "gemma-3-4b": "unloaded"}
    assert "/api/v1/models" in paths
    assert "/api/v0/models" not in paths


def test_lm_studio_state_falls_back_to_the_v0_state_field() -> None:
    routes = lm_studio_routes(models=["qwen3-8b", "gemma-3-4b"], loaded={"gemma-3-4b"}, rest_v1=False)
    with json_route_server(routes) as server:
        states = read_load_states(
            _lm_studio_account(server.base_url), _lm_studio_profile(), http_get_json=_get_json
        )
        paths = [item["path"] for item in server.requests]
    assert states == {"qwen3-8b": "unloaded", "gemma-3-4b": "loaded"}
    assert "/api/v1/models" in paths
    assert "/api/v0/models" in paths


def test_lm_studio_state_is_unknown_when_the_server_cannot_be_read() -> None:
    def refuse(url: str, headers: Mapping[str, str], timeout: float) -> Any:
        raise ConnectionRefusedError(url)

    assert read_lm_studio_rest_states("http://127.0.0.1:9", {}, refuse) == {}
