"""Router genérico de endpoints locales: alta, vista con estado de carga, edición y modelos.

@author Rodrigo Mason
"""

from __future__ import annotations

import threading
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient as PlainTestClient

from local_control_center.agents import local_model_state
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.executions.registration import register_operation
from local_control_center.local_runtimes import endpoints
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.fakes.local_llm_servers import generic_openai_routes, json_route_server, lm_studio_routes
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store

ENDPOINTS = "/api/v1/local-endpoints"


def _seed_models(store, provider_id: str, models: list[str]) -> None:
    accounts = ProviderAccountStore(store.connection)
    for model in models:
        accounts.upsert_model({"providerId": provider_id, "model": model, "enabled": True})


def _by_model(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["model"]: item for item in view["models"]}


def test_create_and_list_report_models_with_their_load_state(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(endpoints, "VIEW_LOAD_STATE_WAIT_S", 5.0)
    routes = lm_studio_routes(models=["qwen3-8b", "gemma-3-4b"], loaded={"qwen3-8b"})
    with json_route_server(routes) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            ENDPOINTS,
            headers=headers,
            json={"catalogId": "lm_studio", "instanceId": "lms-list", "baseUrl": server.base_url},
        )
        _seed_models(store, "lms-list", ["qwen3-8b", "gemma-3-4b"])
        listed = client.get(ENDPOINTS)
        account = ProviderAccountStore(store.connection).get_provider_account("lms-list")
        installation = store.connection.execute(
            "SELECT enabled FROM runtime_installations WHERE runtime_id = 'lms-list'"
        ).fetchone()
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["id"], body["catalogId"], body["enabled"]) == ("lms-list", "lm_studio", True)
    assert (body["locality"], body["networkScope"], body["declaredLocal"]) == ("loopback", "loopback", False)
    assert (body["concurrencyLimit"], body["hasCredential"]) == (1, False)
    assert account["providerCatalogId"] == "lm_studio"
    assert account["providerFamily"] == "openai_compatible"
    assert installation["enabled"] == 1
    view = next(item for item in listed.json()["endpoints"] if item["id"] == "lms-list")
    assert view["loadedModels"] == ["qwen3-8b"]
    models = _by_model(view)
    assert models["qwen3-8b"]["loadState"] == "loaded"
    assert models["gemma-3-4b"]["loadState"] == "unloaded"
    assert models["gemma-3-4b"]["validated"] is False
    assert models["gemma-3-4b"]["isDefault"] is False


def test_cached_load_states_hides_router_aliases_of_canonical_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """La vista muestra solo ids canónicos: un alias del router comparte el estado de su modelo."""
    account = {
        "providerId": "llama-alias",
        "providerCatalogId": "llama_cpp",
        "providerType": "local",
        "apiFormat": "openai_compatible",
        "providerFamily": "openai_compatible",
        "baseUrl": "http://127.0.0.1:1/v1",
        "enabled": True,
        "metadata": {},
    }
    monkeypatch.setattr(
        local_model_state.LOAD_STATE_CACHE,
        "get",
        lambda _account, *, max_wait_s: {
            "gemma-4-26b-a4b": "loaded",
            "local": "loaded",
            "qwen3.8-27b": "unloaded",
        },
    )
    monkeypatch.setattr(local_model_state, "model_aliases_for", lambda _account: {"local": "gemma-4-26b-a4b"})

    states = endpoints.cached_load_states(account)

    assert states == {"gemma-4-26b-a4b": "loaded", "qwen3.8-27b": "unloaded"}
    assert endpoints.loaded_models(states) == ["gemma-4-26b-a4b"]


def test_create_rejects_non_local_presets_missing_urls_duplicates_and_anonymous_writes(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with json_route_server(generic_openai_routes(models=["m-a"])) as server:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        remote = client.post(ENDPOINTS, headers=headers, json={"catalogId": "openai_api"})
        missing_url = client.post(ENDPOINTS, headers=headers, json={"catalogId": "local_openai_compatible"})
        client_identity = client.post(
            ENDPOINTS,
            headers=headers,
            json={"catalogId": "vllm", "baseUrl": server.base_url, "providerCatalogId": "openai_api"},
        )
        payload = {
            "catalogId": "local_openai_compatible",
            "instanceId": "gen-dup",
            "baseUrl": server.base_url,
        }
        first = client.post(ENDPOINTS, headers=headers, json=payload)
        duplicate = client.post(ENDPOINTS, headers=headers, json=payload)
        anonymous = client.post(ENDPOINTS, json={**payload, "instanceId": "gen-anon"})
    assert remote.status_code == 422
    assert missing_url.status_code == 422
    assert client_identity.status_code == 422
    assert first.status_code == 201, first.text
    assert duplicate.status_code == 409
    assert anonymous.status_code == 403


def test_patch_updates_account_installation_credential_and_concurrency(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_LOCAL_ENDPOINT_TEST_TOKEN", "synthetic-fixture")
    with (
        json_route_server(generic_openai_routes(models=["m-a"])) as server,
        json_route_server(generic_openai_routes(models=["m-a"])) as moved,
    ):
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            ENDPOINTS,
            headers=headers,
            json={
                "catalogId": "local_openai_compatible",
                "instanceId": "gen-patch",
                "baseUrl": server.base_url,
            },
        )
        assert created.status_code == 201, created.text
        store.connection.execute(
            "UPDATE provider_accounts SET health_status = 'healthy', last_health_check_at = '2026-09-23T00:00:00Z' "
            "WHERE provider_id = 'gen-patch'"
        )
        renamed = client.patch(
            f"{ENDPOINTS}/gen-patch",
            headers=headers,
            json={
                "displayName": "Lab box",
                "enabled": False,
                "concurrencyLimit": 2,
                "credentialRef": "env:AIDO_LOCAL_ENDPOINT_TEST_TOKEN",
            },
        )
        moved_url = client.patch(f"{ENDPOINTS}/gen-patch", headers=headers, json={"baseUrl": moved.base_url})
        too_many = client.patch(f"{ENDPOINTS}/gen-patch", headers=headers, json={"concurrencyLimit": 0})
        installation = store.connection.execute(
            "SELECT enabled FROM runtime_installations WHERE runtime_id = 'gen-patch'"
        ).fetchone()
        runtime_account = store.connection.execute(
            "SELECT auth_mode, credential_ref FROM runtime_accounts WHERE runtime_id = 'gen-patch'"
        ).fetchone()
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["displayName"] == "Lab box"
    assert renamed.json()["enabled"] is False
    assert renamed.json()["concurrencyLimit"] == 2
    assert renamed.json()["hasCredential"] is True
    assert renamed.json()["healthStatus"] == "unknown"
    assert moved_url.status_code == 200, moved_url.text
    assert moved_url.json()["baseUrl"] == moved.base_url
    assert too_many.status_code == 422
    assert installation["enabled"] == 0
    assert tuple(runtime_account) == ("bearer", "env:AIDO_LOCAL_ENDPOINT_TEST_TOKEN")


def test_model_patch_keeps_a_single_default_and_opt_in_capabilities(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with json_route_server(generic_openai_routes(models=["m-a", "m-b"])) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            ENDPOINTS,
            headers=headers,
            json={
                "catalogId": "local_openai_compatible",
                "instanceId": "gen-models",
                "baseUrl": server.base_url,
            },
        )
        assert created.status_code == 201, created.text
        _seed_models(store, "gen-models", ["m-a", "m-b"])
        first = client.patch(
            f"{ENDPOINTS}/gen-models/models",
            headers=headers,
            json={"model": "m-a", "isDefault": True, "codeEdit": True, "operatorOrder": 2},
        )
        second = client.patch(
            f"{ENDPOINTS}/gen-models/models", headers=headers, json={"model": "m-b", "isDefault": True}
        )
        disabled = client.patch(
            f"{ENDPOINTS}/gen-models/models", headers=headers, json={"model": "m-b", "enabled": False}
        )
        missing = client.patch(f"{ENDPOINTS}/gen-models/models", headers=headers, json={"model": "m-zzz"})
        listed = next(
            item for item in client.get(ENDPOINTS).json()["endpoints"] if item["id"] == "gen-models"
        )
        default_model = LocalModelSettingsRepository(store.connection).default_model("gen-models")
    assert first.status_code == 200, first.text
    assert (first.json()["isDefault"], first.json()["codeEdit"], first.json()["operatorOrder"]) == (
        True,
        True,
        2,
    )
    assert second.json()["isDefault"] is True
    assert disabled.json()["enabled"] is False
    assert missing.status_code == 404
    models = _by_model(listed)
    assert models["m-a"]["isDefault"] is False
    assert models["m-a"]["codeEdit"] is True
    assert models["m-b"]["isDefault"] is True
    assert models["m-b"]["enabled"] is False
    assert default_model == "m-b"


def test_validate_model_is_queued_as_a_local_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    with json_route_server(generic_openai_routes(models=["m-val"])) as server, ExitStack() as stack:
        fixture = stack.enter_context(
            closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
        )
        fixture.init()
        client = stack.enter_context(PlainTestClient(create_app(runtime=fixture, static_dir=None)))
        token = client.get("/api/v1/security/handshake").json()["token"]
        headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}
        created = client.post(
            ENDPOINTS,
            headers=headers,
            json={
                "catalogId": "local_openai_compatible",
                "instanceId": "gen-val",
                "baseUrl": server.base_url,
            },
        )
        accepted = client.post(
            f"{ENDPOINTS}/gen-val/validate-model", headers=headers, json={"model": "m-val"}
        )
        execution = client.get(f"/api/v1/executions/{accepted.json()['executionId']}").json()
    assert created.status_code == 201, created.text
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["operation"] == "local_endpoints.validate_model"
    assert execution["workloadClass"] == "local_model_call"


def test_validate_model_rejects_a_model_the_endpoint_does_not_have(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with json_route_server(generic_openai_routes(models=["m-val"])) as server:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            ENDPOINTS,
            headers=headers,
            json={
                "catalogId": "local_openai_compatible",
                "instanceId": "gen-miss",
                "baseUrl": server.base_url,
            },
        )
        assert created.status_code == 201, created.text
        response = client.post(
            f"{ENDPOINTS}/gen-miss/validate-model", headers=headers, json={"model": "nope"}
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Model not found for gen-miss: nope"


def test_validate_model_operation_registers_for_the_dispatcher(tmp_path: Path) -> None:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "isolated.sqlite")
    try:
        runtime.init()
        register_operation(runtime, "local_endpoints.validate_model")
        spec, handler = runtime.execution_handlers["local_endpoints.validate_model"]
    finally:
        runtime.close()
    assert spec.workload_class == "local_model_call"
    assert spec.result_model is not None
    assert handler.__name__ == "validate_local_model"


LOOPBACK_LLAMA = {
    "providerId": "llama-view",
    "providerCatalogId": "llama_cpp",
    "providerType": "local",
    "apiFormat": "openai_compatible",
    "providerFamily": "openai_compatible",
    "baseUrl": "http://127.0.0.1:1/v1",
    "enabled": True,
    "metadata": {},
}


def test_polled_views_do_not_wait_for_a_server_that_is_slow_to_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El rollup, la lista y los candidatos corren bajo el lock global: nunca esperan la lectura en vivo."""
    gate = threading.Event()

    def reader(_account):
        gate.wait(5)
        return {"gemma-4-26b-a4b": "loaded"}

    monkeypatch.setattr(
        local_model_state, "LOAD_STATE_CACHE", local_model_state.LoadStateCache(reader=reader)
    )
    try:
        started = time.monotonic()
        states = endpoints.load_states_by_provider([LOOPBACK_LLAMA])
        elapsed = time.monotonic() - started
    finally:
        gate.set()
    assert states == {"llama-view": {}}
    assert elapsed < 0.25


class _ForbiddenLoadStates:
    def get(self, account, **_kwargs):
        raise AssertionError(f"A view read the load state of {account['providerId']}")


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"enabled": False}, id="disabled"),
        pytest.param({"baseUrl": "http://192.168.1.50:8080/v1"}, id="undeclared-lan"),
        pytest.param({"metadata": {"endpointKind": "remote"}}, id="endpoint-kind-remote"),
    ],
)
def test_views_never_query_a_disabled_or_remote_endpoint(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    """P14: ``loadedModels`` y las vistas solo consultan cuentas habilitadas, con perfil y no remotas."""
    monkeypatch.setattr(local_model_state, "LOAD_STATE_CACHE", _ForbiddenLoadStates())
    account = {**LOOPBACK_LLAMA, **overrides}

    assert endpoints.cached_load_states(account) == {}
    assert endpoints.load_states_by_provider([account]) == {"llama-view": {}}
