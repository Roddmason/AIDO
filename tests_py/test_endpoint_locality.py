"""La localidad, el alcance de red y el costo self-hosted salen de una sola fuente pura."""

from __future__ import annotations

import sqlite3

import pytest

from local_control_center.agents.endpoint_locality import (
    catalog_entry_for_account,
    credential_transport_allowed,
    effective_connection,
    endpoint_locality,
    endpoint_network_scope,
    is_acceptable_declaration_host,
    is_local_model_runtime,
    is_loopback_endpoint,
    is_self_hosted_inference,
    server_root_url,
)
from local_control_center.agents.local_runtime_causes import (
    LOCAL_RUNTIME_CAUSES,
    LocalRuntimeError,
    local_runtime_cause_of,
)
from local_control_center.agents.provider_catalog import LocalRuntimeProfile, provider_catalog_entry
from local_control_center.agents.providers.factory import ProviderAdapterFactory

DECLARED_AT = "2026-09-23T12:00:00Z"
CONNECTION_ENV_VARS = (
    "AIDO_OLLAMA_BASE_URL",
    "OLLAMA_BASE_URL",
    "OLLAMA_HOST",
    "AIDO_OPENAI_COMPATIBLE_BASE_URL",
    "AIDO_OPENAI_COMPATIBLE_API_KEY",
)


def _llama(base_url: str, **extra) -> dict:
    return {
        "providerId": "llama-test",
        "providerType": "local",
        "providerFamily": "openai_compatible",
        "apiFormat": "openai_compatible",
        "providerCatalogId": "llama_cpp",
        "baseUrl": base_url,
        "metadata": {},
        **extra,
    }


def _declared(host: str) -> dict:
    return {"declaredBy": "operator", "declaredAt": DECLARED_AT, "host": host}


def _no_dns(host: str) -> list[str]:
    raise AssertionError(f"Literal hosts must never be resolved: {host}")


def test_local_runtime_causes_are_a_closed_set():
    assert {
        "local_server_unreachable",
        "model_loading",
        "local_model_load_failed",
        "local_auth_required",
        "context_length_exceeded",
        "insecure_credential_transport",
        "local_endpoint_busy",
        "insufficient_time_for_model_load",
        "local_model_not_validated",
        "local_model_not_selected",
    } == LOCAL_RUNTIME_CAUSES
    error = LocalRuntimeError("local_endpoint_busy", "slot 0 is held")
    assert error.cause == "local_endpoint_busy"
    assert str(error) == "local_endpoint_busy: slot 0 is held"
    with pytest.raises(ValueError, match="Unknown local runtime cause"):
        LocalRuntimeError("remote_is_fine", "nope")


@pytest.mark.parametrize(
    "text,cause",
    [
        ("local_server_unreachable: URLError.", "local_server_unreachable"),
        ("model_loading", "model_loading"),
        ("insecure_credential_transport: a bearer credential cannot travel", "insecure_credential_transport"),
        ("Provider has not passed an explicit health check (model_loading: ...)", None),
        ("local_server_unreachable_extra: nope", None),
        ("", None),
        (None, None),
    ],
)
def test_the_local_cause_is_read_only_from_the_start_of_a_reason(text, cause):
    assert local_runtime_cause_of(text) == cause


def test_llama_cpp_catalog_entry_declares_its_local_probe_profile():
    entry = provider_catalog_entry("llamacpp")
    assert entry is not None
    assert entry.id == "llama_cpp"
    assert entry.local_profile == LocalRuntimeProfile(
        liveness_path="/health",
        health_requires_models=False,
        model_state_source="openai_models_status",
        multi_model="router",
        cold_start_timeout_s=180,
        disable_reasoning_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    assert provider_catalog_entry("ollama").local_profile is None


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8082/v1",
        "http://127.0.0.2:8082/v1",
        "http://localhost:8082/v1",
        "http://[::1]:8082/v1",
        "http://[::ffff:127.0.0.1]:8082/v1",
    ],
)
def test_loopback_hosts_are_local_model_runtimes(base_url):
    account = _llama(base_url)
    assert endpoint_locality(account, resolver=_no_dns) == "loopback"
    assert endpoint_network_scope(account, resolver=_no_dns) == "loopback"
    assert is_local_model_runtime(account, resolver=_no_dns) is True
    assert is_loopback_endpoint(account) is True


@pytest.mark.parametrize(
    "override",
    [{"providerType": "gateway"}, {"metadata": {"endpointKind": "remote"}}],
    ids=["gateway", "declared-remote"],
)
def test_gateways_and_remote_markers_are_remote_even_on_loopback(override):
    account = _llama("http://127.0.0.1:20128/v1", **override)
    assert endpoint_locality(account) == "remote"
    assert is_local_model_runtime(account) is False
    assert is_self_hosted_inference(account) is False
    assert is_loopback_endpoint(account) is True


def test_client_local_marker_cannot_promote_a_lan_host():
    account = _llama("http://192.168.1.50:8082/v1", metadata={"endpointKind": "local"})
    assert endpoint_locality(account, resolver=_no_dns) == "remote"
    assert endpoint_network_scope(account, resolver=_no_dns) == "private_network"
    assert is_local_model_runtime(account, resolver=_no_dns) is False


def test_server_declaration_makes_only_its_private_literal_host_local():
    declared = _llama("http://172.20.0.5:8000/v1", localDeclaration=_declared("172.20.0.5"))
    other_host = _llama("http://172.20.0.6:8000/v1", localDeclaration=_declared("172.20.0.5"))
    public = _llama("http://8.8.8.8:8000/v1", localDeclaration=_declared("8.8.8.8"))
    assert endpoint_locality(declared, resolver=_no_dns) == "declared_local"
    assert endpoint_network_scope(declared, resolver=_no_dns) == "declared_local"
    assert is_local_model_runtime(declared, resolver=_no_dns) is True
    assert endpoint_locality(other_host, resolver=_no_dns) == "remote"
    assert endpoint_locality(public, resolver=_no_dns) == "remote"


@pytest.mark.parametrize(
    "addresses,expected",
    [
        (["192.168.65.254"], "declared_local"),
        (["93.184.216.34"], "remote"),
        ([], "remote"),
        (["192.168.65.254", "93.184.216.34"], "remote"),
    ],
    ids=["private", "public", "unresolved", "mixed"],
)
def test_docker_alias_declaration_is_resolved_again_on_every_call(addresses, expected):
    seen = []

    def resolver(host: str) -> list[str]:
        seen.append(host)
        return addresses

    account = _llama(
        "http://host.docker.internal:1234/v1", localDeclaration=_declared("host.docker.internal")
    )
    assert endpoint_locality(account, resolver=resolver) == expected
    assert endpoint_locality(account, resolver=resolver) == expected
    assert seen == ["host.docker.internal", "host.docker.internal"]


@pytest.mark.parametrize(
    "host,accepted",
    [
        ("10.0.0.5", True),
        ("172.16.3.4", True),
        ("192.168.1.10", True),
        ("169.254.10.1", True),
        ("fd00::5", True),
        ("[fd00::5]", True),
        ("fe80::1", True),
        ("8.8.8.8", False),
        ("127.0.0.1", False),
        ("llama.lan", False),
    ],
)
def test_only_private_or_link_local_literals_are_declarable(host, accepted):
    assert is_acceptable_declaration_host(host, resolver=_no_dns) is accepted


@pytest.mark.parametrize(
    "account,exempt",
    [
        (_llama("http://127.0.0.1:8082/v1"), True),
        (_llama("http://192.168.1.50:8082/v1"), True),
        (_llama("http://llama.example.com:8082/v1"), False),
        (_llama("http://127.0.0.1:8082/v1", metadata={"endpointKind": "remote"}), False),
        (_llama("http://127.0.0.1:8082/v1", providerType="gateway"), False),
        (_llama("http://127.0.0.1:8082/v1", providerType="api"), False),
        (_llama("http://127.0.0.1:8082/v1", providerCatalogId="openai_compatible"), False),
        (
            {
                "providerId": "ollama-desk",
                "providerType": "local",
                "providerFamily": "ollama-desk",
                "apiFormat": "ollama",
                "baseUrl": "http://127.0.0.1:11434",
            },
            True,
        ),
        (
            {
                "providerId": "ollama_remote",
                "providerType": "local",
                "providerFamily": "ollama",
                "apiFormat": "ollama",
                "providerCatalogId": "ollama_remote",
                "baseUrl": "http://192.168.1.60:11434",
            },
            False,
        ),
    ],
    ids=[
        "loopback",
        "private-literal",
        "public-name",
        "marked-remote",
        "gateway",
        "api",
        "generic-catalog",
        "ollama-router-endpoint",
        "ollama-remote-catalog",
    ],
)
def test_self_hosted_cost_exemption_follows_catalog_and_network_scope(account, exempt):
    assert is_self_hosted_inference(account, resolver=_no_dns) is exempt


def test_catalog_identity_prefers_the_server_field_and_keeps_legacy_fallbacks():
    assert catalog_entry_for_account(_llama("http://127.0.0.1:8082/v1")).id == "llama_cpp"
    assert (
        catalog_entry_for_account(
            {"providerFamily": "ollama-desk", "providerId": "ollama-desk", "apiFormat": "ollama"}
        ).id
        == "ollama"
    )
    assert catalog_entry_for_account({"providerFamily": "openai_compatible", "providerId": "x"}).id == (
        "openai_compatible"
    )
    assert (
        catalog_entry_for_account({"providerCatalogId": "missing-entry", "providerFamily": "ollama"}) is None
    )
    assert catalog_entry_for_account({"providerId": "custom-thing"}) is None


def test_legacy_accounts_resolve_by_provider_id_before_format_and_family():
    legacy_llama = {
        "providerId": "llama_cpp",
        "providerType": "local",
        "providerFamily": "openai_compatible",
        "apiFormat": "openai_compatible",
    }
    assert catalog_entry_for_account(legacy_llama).id == "llama_cpp"
    assert catalog_entry_for_account({**legacy_llama, "providerId": "llamacpp"}).id == "llama_cpp"
    assert (
        catalog_entry_for_account(
            {"providerId": "ollama-lan", "providerFamily": "openai_compatible", "apiFormat": "ollama"}
        ).id
        == "ollama"
    )


@pytest.mark.parametrize(
    "account,allowed",
    [
        (_llama("http://192.168.1.50:8082/v1"), True),
        (_llama("http://192.168.1.50:8082/v1", credentialRef="env:TOKEN"), False),
        (_llama("https://llama.example.com/v1", credentialRef="env:TOKEN"), True),
        (_llama("http://127.0.0.1:8082/v1", credentialRef="env:TOKEN"), True),
        (_llama("http://127.0.0.1:20128/v1", credentialRef="env:TOKEN", providerType="gateway"), True),
        (
            _llama(
                "http://172.20.0.5:8000/v1",
                credentialRef="env:TOKEN",
                localDeclaration=_declared("172.20.0.5"),
            ),
            True,
        ),
    ],
    ids=["no-credential", "lan-http", "https", "loopback-http", "loopback-gateway", "declared-http"],
)
def test_bearer_never_travels_over_plain_http_to_a_non_local_host(account, allowed):
    assert credential_transport_allowed(account, resolver=_no_dns) is allowed


@pytest.fixture
def clean_connection_env(monkeypatch):
    for name in CONNECTION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _canonical_ollama(base_url: str, **extra) -> dict:
    return {
        "providerId": "ollama",
        "providerType": "local",
        "providerFamily": "ollama",
        "apiFormat": "ollama",
        "baseUrl": base_url,
        "metadata": {},
        **extra,
    }


def _canonical_openai_compatible(base_url: str, **extra) -> dict:
    return {
        "providerId": "openai_compatible",
        "providerType": "local",
        "providerFamily": "openai_compatible",
        "apiFormat": "openai_compatible",
        "providerCatalogId": "llama_cpp",
        "baseUrl": base_url,
        "metadata": {},
        **extra,
    }


@pytest.mark.parametrize("persisted_url", ["http://127.0.0.1:11434", ""], ids=["loopback-persisted", "empty"])
def test_env_ollama_url_overrides_the_persisted_url_like_the_adapter(clean_connection_env, persisted_url):
    clean_connection_env.setenv("AIDO_OLLAMA_BASE_URL", "http://ollama.example.com:11434")
    account = _canonical_ollama(persisted_url, credentialRef="env:TOKEN")
    assert effective_connection(account) == ("http://ollama.example.com:11434", "env:TOKEN")
    assert endpoint_locality(account, resolver=_no_dns) == "remote"
    assert is_self_hosted_inference(account, resolver=_no_dns) is False
    assert credential_transport_allowed(account, resolver=_no_dns) is False


def test_legacy_local_ollama_without_url_takes_the_env_url_of_its_adapter(clean_connection_env):
    clean_connection_env.setenv("AIDO_OLLAMA_BASE_URL", "http://ollama.example.com:11434")
    account = {**_canonical_ollama(""), "providerId": "local_ollama"}
    assert effective_connection(account)[0] == "http://ollama.example.com:11434"
    assert endpoint_locality(account, resolver=_no_dns) == "remote"


def test_env_openai_compatible_url_overrides_the_persisted_loopback_url(clean_connection_env):
    clean_connection_env.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", "http://llm.example.com/v1")
    account = _canonical_openai_compatible("http://127.0.0.1:8082/v1", credentialRef="env:TOKEN")
    assert endpoint_locality(account, resolver=_no_dns) == "remote"
    assert is_local_model_runtime(account, resolver=_no_dns) is False
    assert is_self_hosted_inference(account, resolver=_no_dns) is False
    assert credential_transport_allowed(account, resolver=_no_dns) is False


def test_env_api_key_counts_as_a_bearer_for_transport(clean_connection_env):
    clean_connection_env.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "dummy-value")
    account = _canonical_openai_compatible("http://llm.example.com/v1")
    assert effective_connection(account) == (
        "http://llm.example.com/v1",
        "env:AIDO_OPENAI_COMPATIBLE_API_KEY",
    )
    assert credential_transport_allowed(account, resolver=_no_dns) is False
    assert credential_transport_allowed({**account, "baseUrl": "http://127.0.0.1:8082/v1"}) is True


def test_endpoint_scoped_accounts_ignore_the_family_env_configuration(clean_connection_env):
    clean_connection_env.setenv("AIDO_OPENAI_COMPATIBLE_BASE_URL", "http://llm.example.com/v1")
    clean_connection_env.setenv("AIDO_OPENAI_COMPATIBLE_API_KEY", "dummy-value")
    account = {**_canonical_openai_compatible("http://127.0.0.1:8082/v1"), "providerId": "llama-test"}
    assert effective_connection(account) == ("http://127.0.0.1:8082/v1", "")
    assert endpoint_locality(account, resolver=_no_dns) == "loopback"


@pytest.mark.parametrize(
    "account,env",
    [
        (
            _canonical_ollama("http://127.0.0.1:11434"),
            {"AIDO_OLLAMA_BASE_URL": "http://ollama.example.com:11434"},
        ),
        (
            _canonical_openai_compatible("http://127.0.0.1:8082/v1", credentialRef="env:TOKEN"),
            {
                "AIDO_OPENAI_COMPATIBLE_BASE_URL": "http://llm.example.com/v1",
                "AIDO_OPENAI_COMPATIBLE_API_KEY": "dummy-value",
            },
        ),
        (_canonical_openai_compatible("http://127.0.0.1:8082/v1", credentialRef="env:TOKEN"), {}),
        ({**_canonical_openai_compatible("http://127.0.0.1:8082/v1"), "providerId": "llama-test"}, {}),
        (
            {
                "providerId": "openai",
                "providerType": "api",
                "providerFamily": "openai",
                "apiFamily": "chat_completions",
                "baseUrl": "",
                "credentialRef": "env:TOKEN",
            },
            {},
        ),
    ],
    ids=[
        "ollama-env-url",
        "compatible-env-url-and-key",
        "compatible-persisted",
        "endpoint-scoped",
        "family-default",
    ],
)
def test_connection_matches_the_adapter_factory_resolution(clean_connection_env, account, env):
    for name, value in env.items():
        clean_connection_env.setenv(name, value)
    connection = sqlite3.connect(":memory:")
    try:
        factory_base_url, factory_credential_ref = ProviderAdapterFactory(
            connection
        )._connection_configuration(account)
    finally:
        connection.close()
    assert effective_connection(account) == (factory_base_url or "", factory_credential_ref or "")


def test_builtin_ollama_without_url_uses_the_adapter_default(clean_connection_env):
    account = {
        "providerId": "ollama",
        "providerType": "local",
        "providerFamily": "ollama",
        "apiFormat": "ollama",
        "baseUrl": "",
    }
    assert endpoint_locality(account) == "loopback"
    assert is_self_hosted_inference(account) is True


@pytest.mark.parametrize(
    "base_url,root",
    [
        ("http://127.0.0.1:8082/v1", "http://127.0.0.1:8082"),
        ("http://127.0.0.1:8082/v1/", "http://127.0.0.1:8082"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("", ""),
    ],
)
def test_server_root_drops_only_the_openai_suffix(base_url, root):
    assert server_root_url(base_url) == root
