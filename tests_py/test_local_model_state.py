"""Estado de carga de modelos locales: parseo defensivo por fuente y caché compartida no bloqueante.

@author Rodrigo Mason
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.local_model_state import (
    LOAD_STATE_READERS,
    LoadStateCache,
    model_aliases_for,
    read_load_states,
)
from local_control_center.agents.provider_catalog import LocalRuntimeProfile

ACCOUNT = {
    "providerId": "llama_cpp",
    "providerType": "local",
    "baseUrl": "http://127.0.0.1:1/v1",
    "credentialRef": "",
    "metadata": {},
}
ROUTER = LocalRuntimeProfile(
    liveness_path="/health",
    health_requires_models=False,
    model_state_source="openai_models_status",
    multi_model="router",
    cold_start_timeout_s=180,
)
SINGLE = LocalRuntimeProfile(
    liveness_path="/health",
    health_requires_models=True,
    model_state_source="single_model",
    multi_model="single",
    cold_start_timeout_s=60,
)
UNKNOWN = LocalRuntimeProfile(
    liveness_path="/v1/models",
    health_requires_models=True,
    model_state_source="none",
    multi_model="unknown",
    cold_start_timeout_s=180,
)
ROUTER_MODELS = {
    "object": "list",
    "data": [
        {"id": "gemma-4-26b-a4b", "owned_by": "llamacpp", "status": {"value": "loaded"}},
        {"id": "qwen3-8b", "owned_by": "llamacpp", "status": {"value": "unloaded"}},
        {"id": "glm-4.7-flash", "owned_by": "llamacpp", "status": {"value": "loading"}},
        {"id": "no-status", "owned_by": "llamacpp"},
        {"id": "odd-status", "owned_by": "llamacpp", "status": {"value": "sleeping"}},
    ],
}


def _http(payload: Any, seen: list | None = None):
    def get(url, headers, timeout_s):
        if seen is not None:
            seen.append((url, dict(headers), timeout_s))
        return payload

    return get


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_router_status_maps_known_values_and_marks_the_rest_unknown():
    seen: list = []
    states = read_load_states(ACCOUNT, ROUTER, http_get_json=_http(ROUTER_MODELS, seen))
    assert states == {
        "gemma-4-26b-a4b": "loaded",
        "qwen3-8b": "unloaded",
        "glm-4.7-flash": "loading",
        "no-status": "unknown",
        "odd-status": "unknown",
    }
    assert seen[0][0] == "http://127.0.0.1:1/v1/models"
    assert "Authorization" not in seen[0][1]


def test_router_aliases_share_the_load_state_of_their_canonical_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(local_model_state, "_LISTED_ALIASES", {})
    payload = {
        "object": "list",
        "data": [
            {"id": "gemma-4-26b-a4b", "aliases": ["local"], "status": {"value": "loaded"}},
            {"id": "qwen3-8b", "aliases": ["gemma-4-26b-a4b", "", 7], "status": {"value": "unloaded"}},
        ],
    }
    states = read_load_states(ACCOUNT, ROUTER, http_get_json=_http(payload))
    assert states == {"gemma-4-26b-a4b": "loaded", "local": "loaded", "qwen3-8b": "unloaded"}
    assert model_aliases_for(ACCOUNT) == {"local": "gemma-4-26b-a4b"}
    assert model_aliases_for({**ACCOUNT, "baseUrl": "http://127.0.0.1:9999/v1"}) == {}


def test_single_model_server_reports_its_only_model_as_loaded():
    payload = {"data": [{"id": "Qwen/Qwen3-8B", "owned_by": "vllm"}]}
    assert read_load_states(ACCOUNT, SINGLE, http_get_json=_http(payload)) == {"Qwen/Qwen3-8B": "loaded"}
    two = {"data": [{"id": "a"}, {"id": "b"}]}
    assert read_load_states(ACCOUNT, SINGLE, http_get_json=_http(two)) == {}


def test_a_generic_server_is_unknown_without_any_request():
    seen: list = []
    assert read_load_states(ACCOUNT, UNKNOWN, http_get_json=_http(ROUTER_MODELS, seen)) == {}
    assert seen == []


def test_readers_are_dispatched_by_source_with_the_root_url(monkeypatch: pytest.MonkeyPatch):
    assert {"openai_models_status", "single_model", "none"} <= set(LOAD_STATE_READERS)
    calls: list = []

    def reader(root_url, headers, http_get_json):
        calls.append((root_url, dict(headers)))
        return {"custom": "loading"}

    monkeypatch.setitem(LOAD_STATE_READERS, "openai_models_status", reader)
    assert read_load_states(ACCOUNT, ROUTER, http_get_json=_http(ROUTER_MODELS)) == {"custom": "loading"}
    assert calls == [("http://127.0.0.1:1", {})]
    monkeypatch.delitem(LOAD_STATE_READERS, "none")
    assert read_load_states(ACCOUNT, UNKNOWN, http_get_json=_http(ROUTER_MODELS)) == {}


@pytest.mark.parametrize("payload", [{"data": "nope"}, ["unexpected"], None])
def test_a_malformed_listing_is_unknown(payload):
    assert read_load_states(ACCOUNT, ROUTER, http_get_json=_http(payload)) == {}


def test_the_bearer_is_sent_only_when_the_ref_resolves(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIDO_LOCAL_STATE_TEST_TOKEN", "synthetic-fixture")
    seen: list = []
    account = {**ACCOUNT, "credentialRef": "env:AIDO_LOCAL_STATE_TEST_TOKEN"}
    read_load_states(account, ROUTER, http_get_json=_http(ROUTER_MODELS, seen))
    assert seen[0][1]["Authorization"] == "Bearer synthetic-fixture"


def test_the_cache_reuses_a_fresh_read_and_refreshes_after_the_ttl():
    calls: list[str] = []
    clock = _Clock()

    def reader(account):
        calls.append(account["providerId"])
        return {"gemma": "loaded"}

    cache = LoadStateCache(ttl_s=10.0, reader=reader, clock=clock)
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "loaded"}
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "loaded"}
    assert calls == ["llama_cpp"]
    clock.now += 11
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "loaded"}
    assert calls == ["llama_cpp", "llama_cpp"]


def test_a_slow_server_reads_as_unknown_without_blocking_and_with_a_single_flight():
    gate = threading.Event()
    calls: list[int] = []

    def reader(account):
        calls.append(1)
        gate.wait(5)
        return {"gemma": "loaded"}

    cache = LoadStateCache(ttl_s=10.0, reader=reader)
    assert cache.get(ACCOUNT, max_wait_s=0.01) == {}
    assert cache.get(ACCOUNT, max_wait_s=0.01) == {}
    gate.set()
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "loaded"}
    assert calls == [1]


def test_a_failing_read_is_unknown():
    def reader(account):
        raise OSError("connection refused")

    assert LoadStateCache(ttl_s=10.0, reader=reader).get(ACCOUNT, max_wait_s=2.0) == {}


def test_a_polling_reader_gets_the_expired_reading_at_once_while_it_refreshes():
    """Las vistas consultadas por polling no esperan: reciben la última lectura mientras se refresca."""
    gate = threading.Event()
    calls: list[int] = []
    clock = _Clock()

    def reader(account):
        calls.append(1)
        if len(calls) > 1:
            gate.wait(5)
            return {"gemma": "unloaded"}
        return {"gemma": "loaded"}

    cache = LoadStateCache(ttl_s=10.0, reader=reader, clock=clock)
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "loaded"}
    clock.now += 11
    try:
        assert cache.get(ACCOUNT, max_wait_s=0.0, allow_stale=True) == {"gemma": "loaded"}
        assert cache.get(ACCOUNT, max_wait_s=0.01) == {}
    finally:
        gate.set()
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "unloaded"}
    assert calls == [1, 1]


def test_a_polling_reader_never_gets_a_reading_of_another_base_url():
    """La lectura vieja se sirve por (cuenta, URL): cambiar la URL nunca hereda el estado anterior."""
    gate = threading.Event()
    clock = _Clock()
    moved = {**ACCOUNT, "baseUrl": "http://127.0.0.1:2/v1"}

    def reader(account):
        if account["baseUrl"] == moved["baseUrl"]:
            gate.wait(5)
            return {}
        return {"gemma": "loaded"}

    cache = LoadStateCache(ttl_s=10.0, reader=reader, clock=clock)
    assert cache.get(ACCOUNT, max_wait_s=2.0) == {"gemma": "loaded"}
    clock.now += 11
    try:
        assert cache.get(moved, max_wait_s=0.0, allow_stale=True) == {}
    finally:
        gate.set()


ENV_BASE_URL = "AIDO_OPENAI_COMPATIBLE_BASE_URL"
ENV_API_KEY = "AIDO_OPENAI_COMPATIBLE_API_KEY"
LAN_BASE_URL = "http://192.168.1.50/v1"
CANONICAL = {
    "providerId": "openai_compatible",
    "providerType": "local",
    "providerFamily": "openai_compatible",
    "apiFormat": "openai_compatible",
    "credentialRef": "env:AIDO_LOCAL_STATE_TEST_TOKEN",
    "metadata": {},
}


@pytest.fixture
def env_connection(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (ENV_BASE_URL, ENV_API_KEY):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AIDO_LOCAL_STATE_TEST_TOKEN", "synthetic-fixture")
    monkeypatch.setattr(local_model_state, "_LISTED_ALIASES", {})
    return monkeypatch


def test_the_reading_goes_to_the_env_url_the_transport_guard_validated(env_connection):
    """El lector consulta la URL efectiva del adapter (entorno > cuenta), la misma que evalúa el guard."""
    env_connection.setenv(ENV_BASE_URL, "http://127.0.0.1:1/v1")
    account = {**CANONICAL, "baseUrl": LAN_BASE_URL}
    seen: list = []
    payload = {"data": [{"id": "gemma", "aliases": ["local"], "status": {"value": "loaded"}}]}
    read_load_states(account, ROUTER, http_get_json=_http(payload, seen))
    assert [url for url, _headers, _timeout in seen] == ["http://127.0.0.1:1/v1/models"]
    assert seen[0][1]["Authorization"] == "Bearer synthetic-fixture"
    assert model_aliases_for(account) == {"local": "gemma"}


def test_no_bearer_travels_by_http_to_an_undeclared_lan_host(env_connection):
    """Referencia resuelta + http a un host no loopback ni declarado ⇒ la lectura sale sin bearer."""
    env_connection.setenv(ENV_BASE_URL, LAN_BASE_URL)
    account = {**CANONICAL, "baseUrl": "http://127.0.0.1:1/v1"}
    seen: list = []
    read_load_states(account, ROUTER, http_get_json=_http(ROUTER_MODELS, seen))
    assert seen[0][0] == "http://192.168.1.50/v1/models"
    assert "Authorization" not in seen[0][1]


def test_the_cache_key_follows_the_effective_url(env_connection):
    """Cambiar la URL del entorno nunca sirve la lectura del servidor anterior."""
    reads: list[str] = []

    def reader(account):
        reads.append(account["baseUrl"])
        return {"gemma": "loaded"}

    cache = LoadStateCache(ttl_s=10.0, reader=reader)
    account = {**CANONICAL, "baseUrl": LAN_BASE_URL}
    env_connection.setenv(ENV_BASE_URL, "http://127.0.0.1:1/v1")
    assert cache.get(account, max_wait_s=2.0) == {"gemma": "loaded"}
    env_connection.setenv(ENV_BASE_URL, "http://127.0.0.1:2/v1")
    assert cache.get(account, max_wait_s=2.0) == {"gemma": "loaded"}
    assert len(reads) == 2
