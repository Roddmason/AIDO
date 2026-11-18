"""La identidad de catálogo y la declaración local son campos del servidor, no metadata del cliente."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_provider_setup_catalog import auth_headers, create_client

DECLARATION = {"declaredBy": "operator", "declaredAt": "2026-09-23T12:00:00Z", "host": "192.168.1.50"}


def test_from_catalog_writes_catalog_identity_as_a_server_field(tmp_path: Path, monkeypatch) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={
            "providerId": "llama_cpp",
            "enabled": True,
            "metadata": {
                "providerCatalogId": "openai_compatible",
                "endpointKind": "local",
                "operatorNote": "desk",
            },
        },
    )
    assert created.status_code == 201, created.text
    provider = created.json()["provider"]
    assert provider["providerCatalogId"] == "llama_cpp"
    assert provider["localDeclaration"] is None
    assert provider["localConcurrencyLimit"] is None
    assert "providerCatalogId" not in provider["metadata"]
    assert "endpointKind" not in provider["metadata"]
    assert provider["metadata"]["operatorNote"] == "desk"

    patched = client.patch(
        "/api/v1/model-gateway/providers/llama_cpp",
        headers=headers,
        json={"metadata": {"providerCatalogId": "ollama", "endpointKind": "local"}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["provider"]["providerCatalogId"] == "llama_cpp"
    assert "providerCatalogId" not in patched.json()["provider"]["metadata"]

    with closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))) as connection:
        installation = RuntimeConfigRepository(connection).get_installation("llama_cpp")
    assert installation["enabled"] is True
    assert installation["kind"] == "local"
    assert installation["configurationSource"] == "local_runtime_catalog"


def test_store_sanitizes_client_metadata_and_preserves_server_fields(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "fields.sqlite")) as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "lan-llama",
                "providerType": "local",
                "providerFamily": "openai_compatible",
                "apiFormat": "openai_compatible",
                "baseUrl": "http://192.168.1.50:8082/v1",
                "metadata": {"endpointKind": "remote", "providerCatalogId": "ollama"},
            }
        )
        store.set_provider_catalog_id("lan-llama", "llamacpp")
        store.set_local_declaration("lan-llama", DECLARATION)
        store.set_local_concurrency_limit("lan-llama", 2)
        patched = store.patch_provider_account(
            "lan-llama",
            {
                "displayName": "LAN llama",
                "providerCatalogId": "openai_compatible",
                "localConcurrencyLimit": 9,
            },
        )
        with pytest.raises(ValueError, match="Unknown provider catalog id"):
            store.set_provider_catalog_id("lan-llama", "not-a-catalog-entry")
        with pytest.raises(ValueError, match="localConcurrencyLimit"):
            store.set_local_concurrency_limit("lan-llama", 0)
        with pytest.raises(ValueError, match="missing"):
            store.set_local_declaration("lan-llama", {"declaredBy": "operator"})
        cleared = store.set_local_declaration("lan-llama", None)
    assert patched["providerCatalogId"] == "llama_cpp"
    assert patched["localDeclaration"] == DECLARATION
    assert patched["localConcurrencyLimit"] == 2
    assert patched["metadata"] == {"endpointKind": "remote"}
    assert cleared["localDeclaration"] is None


def test_phase77_moves_catalog_identity_out_of_client_metadata(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "legacy.sqlite")) as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "llama_cpp",
                "providerType": "local",
                "providerFamily": "openai_compatible",
                "apiFormat": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        connection.execute(
            "UPDATE provider_accounts SET provider_catalog_id = NULL, metadata_json = ? "
            "WHERE provider_id = 'llama_cpp'",
            ('{"providerCatalogId": "llama_cpp", "endpointKind": "local", "keep": 1}',),
        )
        connection.execute("DELETE FROM runtime_installations WHERE runtime_id = 'llama_cpp'")
        connection.execute("DELETE FROM schema_migrations WHERE version = 77")
        initialize_platform_schema(connection)
        account = ProviderAccountStore(connection).get_provider_account("llama_cpp")
        installation = RuntimeConfigRepository(connection).get_installation("llama_cpp")
    assert account["providerCatalogId"] == "llama_cpp"
    assert account["metadata"] == {"keep": 1}
    assert installation["enabled"] is True
    assert installation["kind"] == "local"
