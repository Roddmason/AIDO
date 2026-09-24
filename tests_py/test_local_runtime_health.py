"""llama.cpp local: guard de configuración, salud por liveness y sync que falla cerrado."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from local_control_center.agents.model_gateway import ModelGateway
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_catalog import LocalRuntimeProfile
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_adapters.models import RuntimeExecutionRequest
from local_control_center.agents.runtime_adapters.provider_factory import ProviderFactoryAdapter
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import running_llama_router
from tests_py.test_provider_setup_catalog import auth_headers, create_client

LAN_BASE_URL = "http://192.168.1.50:8082/v1"
TOKEN_ENV = "AIDO_LOCAL_RUNTIME_TEST_TOKEN"


def _create_llama(client, headers, base_url: str) -> dict:
    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={"providerId": "llama_cpp", "baseUrl": base_url, "enabled": True},
    )
    assert response.status_code == 201, response.text
    return response.json()["provider"]


def _platform_connection():
    return closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])))


def _lan_llama(connection) -> None:
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": "lan-llama",
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "baseUrl": LAN_BASE_URL,
            "credentialRef": f"env:{TOKEN_ENV}",
            "enabled": True,
        }
    )
    store.set_provider_catalog_id("lan-llama", "llama_cpp")


def test_llama_cpp_health_uses_the_router_liveness_and_lists_models(tmp_path, monkeypatch):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with running_llama_router() as (root, router):
        _create_llama(client, headers, f"{root}/v1")
        response = client.post("/api/v1/model-gateway/providers/llama_cpp/health-check", headers=headers)
    assert response.status_code == 200, response.text
    health = response.json()["health"]
    assert health["status"] == "available"
    assert health["healthStatus"] == "healthy"
    paths = [(method, path) for method, path, _authorization in router.requests]
    assert ("GET", "/health") in paths
    assert ("GET", "/v1/models") in paths
    with _platform_connection() as connection:
        account = ProviderAccountStore(connection).get_provider_account("llama_cpp")
    assert account["healthStatus"] == "healthy"
    assert account["lastHealthCheckAt"]


def test_loading_model_is_reported_without_opening_the_failure_cooldown(tmp_path, monkeypatch):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with running_llama_router(health_status=503) as (root, _router):
        _create_llama(client, headers, f"{root}/v1")
        response = client.post("/api/v1/model-gateway/providers/llama_cpp/health-check", headers=headers)
    health = response.json()["health"]
    assert health["status"] == "model_loading"
    assert health["healthStatus"] == "model_loading"
    assert health["message"].startswith("model_loading")
    with _platform_connection() as connection:
        assert "llama_cpp" not in QuotaManager(connection).providers_in_cooldown()
        assert (
            ProviderAccountStore(connection).get_provider_account("llama_cpp")["healthStatus"]
            == "model_loading"
        )


def test_router_api_key_without_an_account_credential_is_local_auth_required(tmp_path, monkeypatch):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with running_llama_router(api_key="router-secret") as (root, _router):
        _create_llama(client, headers, f"{root}/v1")
        response = client.post("/api/v1/model-gateway/providers/llama_cpp/health-check", headers=headers)
    health = response.json()["health"]
    assert health["healthStatus"] == "misconfigured"
    assert health["message"].startswith("local_auth_required")


def test_an_enabled_model_the_router_is_loading_reports_model_loading(tmp_path, monkeypatch):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with running_llama_router() as (root, router):
        _create_llama(client, headers, f"{root}/v1")
        synced = client.post("/api/v1/provider-accounts/llama_cpp/sync-models", headers=headers)
        router.models[1]["status"]["value"] = "loading"
        response = client.post("/api/v1/model-gateway/providers/llama_cpp/health-check", headers=headers)
    assert synced.status_code == 200, synced.text
    health = response.json()["health"]
    assert health["healthStatus"] == "model_loading"
    assert health["message"].startswith("model_loading")


def test_a_single_model_server_without_the_configured_model_fails_to_load_it():
    from local_control_center.agents.local_runtime_health import probe_local_runtime

    profile = LocalRuntimeProfile(
        liveness_path="/health",
        health_requires_models=True,
        model_state_source="single_model",
        multi_model="single",
        cold_start_timeout_s=60,
    )
    with running_llama_router() as (root, _router):
        account = {"providerId": "single-model", "providerType": "local", "baseUrl": f"{root}/v1"}
        served = probe_local_runtime(account, profile, expected_models=["gpt-oss-20b"])
        missing = probe_local_runtime(account, profile, expected_models=["mistral-7b"])
    assert served.health_status == "healthy"
    assert (missing.health_status, missing.cause) == ("offline", "local_model_load_failed")
    assert missing.message.startswith("local_model_load_failed")


def test_sync_probes_liveness_first_and_enables_the_discovered_models(tmp_path, monkeypatch):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with running_llama_router() as (root, router):
        _create_llama(client, headers, f"{root}/v1")
        response = client.post("/api/v1/provider-accounts/llama_cpp/sync-models", headers=headers)
    assert response.status_code == 200, response.text
    assert {item["model"] for item in response.json()["models"]} == {
        "gemma-4-26b-a4b",
        "gpt-oss-20b",
        "qwen3.8-27b",
    }
    paths = [path for _method, path, _authorization in router.requests]
    assert paths.index("/health") < paths.index("/v1/models")
    with _platform_connection() as connection:
        stored = ProviderAccountStore(connection).list_models("llama_cpp")
    assert sorted(row["model"] for row in stored) == ["gemma-4-26b-a4b", "gpt-oss-20b", "qwen3.8-27b"]
    assert all(row["enabled"] for row in stored)


def test_sync_against_a_stopped_server_fails_instead_of_reporting_zero_models(tmp_path, monkeypatch):
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with running_llama_router() as (root, _router):
        stopped_base_url = f"{root}/v1"
    _create_llama(client, headers, stopped_base_url)
    response = client.post("/api/v1/provider-accounts/llama_cpp/sync-models", headers=headers)
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "local_server_unreachable"
    with _platform_connection() as connection:
        store = ProviderAccountStore(connection)
        assert store.list_models("llama_cpp") == []
        account = store.get_provider_account("llama_cpp")
    assert account["healthStatus"] == "offline"
    assert account["lastError"].startswith("local_server_unreachable")


def test_bearer_over_http_to_an_undeclared_lan_host_fails_closed_in_the_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "synthetic-local-token")
    with closing(open_sqlite_connection(tmp_path / "insecure.sqlite")) as connection:
        initialize_platform_schema(connection)
        _lan_llama(connection)
        health = ModelGateway(connection).provider_health("lan-llama")
    assert health["status"] == "blocked"
    assert health["message"].startswith("insecure_credential_transport")


def test_sync_never_sends_a_bearer_over_http_to_an_undeclared_lan_host(tmp_path, monkeypatch):
    from local_control_center.agents import local_runtime_health

    monkeypatch.setenv(TOKEN_ENV, "synthetic-local-token")
    attempts: list[str] = []

    def record_attempt(url, _headers, _timeout_s):
        attempts.append(url)
        raise OSError("this test never opens a network connection")

    monkeypatch.setattr(local_runtime_health, "_get_json", record_attempt)
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _platform_connection() as connection:
        _lan_llama(connection)
    response = client.post("/api/v1/provider-accounts/lan-llama/sync-models", headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "insecure_credential_transport"
    assert attempts == []
    with _platform_connection() as connection:
        account = ProviderAccountStore(connection).get_provider_account("lan-llama")
    assert account["lastError"].startswith("insecure_credential_transport")


def test_bearer_over_http_to_an_undeclared_lan_host_fails_closed_in_the_broker_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "synthetic-local-token")
    calls = []
    monkeypatch.setattr(
        ProviderAdapterFactory,
        "resolve_for_execution",
        lambda *_args: SimpleNamespace(
            base_url=LAN_BASE_URL, credential_ref=f"env:{TOKEN_ENV}", chat_completion=calls.append
        ),
    )
    with closing(open_sqlite_connection(tmp_path / "insecure-adapter.sqlite")) as connection:
        initialize_platform_schema(connection)
        _lan_llama(connection)
        result = ProviderFactoryAdapter(
            provider_family="openai_compatible", display_name="llama.cpp", connection=connection
        ).execute(
            RuntimeExecutionRequest(
                projectId="project-local",
                workspaceId="workspace-local",
                workspacePath=str(tmp_path),
                capability="chat",
                input={
                    "providerId": "lan-llama",
                    "model": "gemma-4-26b-a4b",
                    "messages": [{"role": "user", "content": "Reply OK."}],
                },
            )
        )
    assert result.status == "blocked"
    assert result.reason == "insecure_credential_transport"
    assert calls == []
