"""Los contratos de agentes y el estado del runtime tratan a un runtime local verificado como no-red."""

from __future__ import annotations

from contextlib import closing

import pytest

from local_control_center.agents import architect_agent, developer_agent, product_owner_agent
from local_control_center.agents.architect_agent import ArchitectAgentRunner
from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.product_owner_agent import ProductOwnerAgentRunner
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_selection import runtime_requires_network
from local_control_center.agents.runtime_status import _api_provider_status
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

LOCAL_STATUS = {
    "id": "llama_cpp",
    "providerFamily": "openai_compatible",
    "kind": "local",
    "localModelRuntime": True,
}
API_STATUS = {"id": "openai_compatible", "providerFamily": "openai_compatible", "kind": "api"}


@pytest.mark.parametrize(
    "runtime,remote_families,expected",
    [
        (LOCAL_STATUS, {"openai_compatible"}, False),
        ({**LOCAL_STATUS, "localModelRuntime": False}, set(), True),
        (API_STATUS, {"openai_compatible"}, True),
        ({"id": "ollama", "providerFamily": "ollama", "kind": "local"}, {"openai_compatible"}, False),
        ({"id": "ollama-lan", "providerFamily": "ollama", "kind": "gateway"}, set(), True),
    ],
    ids=["verified-local", "unverified-local", "api", "legacy-ollama", "remote-ollama"],
)
def test_network_requirement_follows_the_runtime_locality(runtime, remote_families, expected):
    assert runtime_requires_network(runtime, remote_families) is expected


def test_runtime_status_marks_local_accounts_and_lists_their_enabled_models(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "status.sqlite")) as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        for provider, base_url in (
            ("llama_cpp", "http://127.0.0.1:1/v1"),
            ("lan-llama", "http://192.168.1.50:8082/v1"),
        ):
            store.upsert_provider_account(
                {
                    "providerId": provider,
                    "providerType": "local",
                    "providerFamily": "openai_compatible",
                    "apiFormat": "openai_compatible",
                    "baseUrl": base_url,
                    "enabled": True,
                }
            )
            store.set_provider_catalog_id(provider, "llama_cpp")
        for model, enabled in (("qwen3.8-27b", True), ("gemma-4-26b-a4b", True), ("gpt-oss-20b", False)):
            store.upsert_model({"providerId": "llama_cpp", "model": model, "enabled": enabled})
        local = _api_provider_status(
            connection,
            store.get_provider_account("llama_cpp"),
            {"enabled": True},
            ["chat"],
            {"allowed": True},
        )
        lan = _api_provider_status(
            connection,
            store.get_provider_account("lan-llama"),
            {"enabled": True},
            ["chat"],
            {"allowed": True},
        )
    assert (local["localModelRuntime"], local["locality"]) == (True, "loopback")
    assert local["models"] == ["gemma-4-26b-a4b", "qwen3.8-27b"]
    assert (lan["localModelRuntime"], lan["locality"]) == (False, "remote")
    assert "models" not in lan
    assert (local["selfHostedInference"], lan["selfHostedInference"]) == (True, True)


def test_runtime_status_reason_starts_with_the_local_cause_code(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "cause.sqlite")) as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "llama_cpp",
                "providerType": "local",
                "providerFamily": "openai_compatible",
                "apiFormat": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        store.set_provider_catalog_id("llama_cpp", "llama_cpp")
        store.upsert_model({"providerId": "llama_cpp", "model": "gemma-4-26b-a4b", "enabled": True})
        store.record_health_check(
            provider_id="llama_cpp",
            status="offline",
            payload={"healthStatus": "offline", "lastError": "local_server_unreachable: URLError."},
        )
        status = _api_provider_status(
            connection,
            store.get_provider_account("llama_cpp"),
            {"enabled": True},
            ["chat"],
            {"allowed": True},
        )
    assert status["reason"] == "local_server_unreachable: URLError."
    assert status["executable"] is False


@pytest.mark.parametrize(
    "module", [architect_agent, developer_agent, product_owner_agent], ids=["architect", "developer", "po"]
)
def test_runtime_mode_is_local_for_verified_local_runtimes_other_than_ollama(module):
    assert module._runtime_mode(LOCAL_STATUS) == "local"
    assert module._runtime_mode({**LOCAL_STATUS, "localModelRuntime": False}) != "local"
    ollama = {"id": "ollama", "providerFamily": "ollama", "kind": "local", "localModelRuntime": True}
    assert module._runtime_mode(ollama) == "ollama"


@pytest.mark.parametrize(
    "runner_class,method",
    [
        (DeveloperAgentRunner, "_create_profile"),
        (ArchitectAgentRunner, "_ensure_profile"),
        (ProductOwnerAgentRunner, "_ensure_profile"),
    ],
    ids=["developer", "architect", "product-owner"],
)
def test_agent_profiles_do_not_open_remote_access_for_local_model_runtimes(tmp_path, runner_class, method):
    with closing(open_sqlite_connection(tmp_path / "profiles.sqlite")) as connection:
        initialize_platform_schema(connection)
        runner = runner_class(connection, root=tmp_path)
        local_profile = getattr(runner, method)(LOCAL_STATUS)
        remote_profile = getattr(runner, method)(API_STATUS)
    assert local_profile["allowRemote"] is False
    assert remote_profile["allowRemote"] is True
