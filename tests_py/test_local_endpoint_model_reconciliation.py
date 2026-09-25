"""Envoltorio Ollama y sync genérico: familia real, enabled del operador y modelos ausentes.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.local_runtimes.endpoints import reconcile_absent_models
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import json_route_server
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store
from tests_py.test_ollama_endpoints_api import run_ollama_tags_server


def _rows(store, provider_id: str) -> dict[str, dict[str, Any]]:
    return {row["model"]: row for row in ProviderAccountStore(store.connection).list_models(provider_id)}


def test_ollama_endpoint_stores_its_real_family_and_catalog_identity(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, _handler, server = run_ollama_tags_server(models=["llama3:latest"])
    try:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json={"id": "ollama-fam", "displayName": "fam", "baseUrl": base_url, "kind": "local"},
        )
        account = ProviderAccountStore(store.connection).get_provider_account("ollama-fam")
    finally:
        server.shutdown()
        server.server_close()
    assert created.status_code == 201, created.text
    assert account["providerFamily"] == "ollama"
    assert account["providerCatalogId"] == "ollama"


def test_ollama_sync_keeps_operator_choices_and_disables_absent_models(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, handler, server = run_ollama_tags_server(models=["a:1", "b:1"])
    try:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        client.post(
            "/api/v1/ollama/endpoints",
            headers=headers,
            json={"id": "ollama-rec", "displayName": "rec", "baseUrl": base_url, "kind": "local"},
        )
        first = client.post("/api/v1/ollama/endpoints/ollama-rec/sync-models", headers=headers)
        ProviderAccountStore(store.connection).patch_model("ollama-rec:b:1", {"enabled": False})
        handler.models = ["b:1", "c:1"]
        second = client.post("/api/v1/ollama/endpoints/ollama-rec/sync-models", headers=headers)
        after_second = _rows(store, "ollama-rec")
        handler.models = ["a:1", "b:1", "c:1"]
        third = client.post("/api/v1/ollama/endpoints/ollama-rec/sync-models", headers=headers)
        after_third = _rows(store, "ollama-rec")
    finally:
        server.shutdown()
        server.server_close()
    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)
    assert after_second["a:1"]["enabled"] is False
    assert after_second["a:1"]["source"] == "endpoint_absent"
    assert after_second["b:1"]["enabled"] is False
    assert after_second["b:1"]["source"] == "operator_override"
    assert after_second["c:1"]["enabled"] is True
    assert after_third["a:1"]["enabled"] is True
    assert after_third["b:1"]["enabled"] is False


def test_generic_local_sync_disables_models_the_server_stopped_listing(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = ["m-a", "m-b"]

    def models(_record: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {"object": "list", "data": [{"id": item, "object": "model"} for item in listed]}

    with json_route_server({("GET", "/v1/models"): models}) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/provider-accounts/from-catalog",
            headers=headers,
            json={
                "providerId": "local_openai_compatible",
                "instanceId": "generic-rec",
                "baseUrl": server.base_url,
                "enabled": True,
            },
        )
        first = client.post("/api/v1/provider-accounts/generic-rec/sync-models", headers=headers)
        listed.remove("m-a")
        second = client.post("/api/v1/provider-accounts/generic-rec/sync-models", headers=headers)
        rows = _rows(store, "generic-rec")
    assert created.status_code == 201, created.text
    assert (first.status_code, second.status_code) == (200, 200), second.text
    assert rows["m-a"]["enabled"] is False
    assert rows["m-a"]["source"] == "endpoint_absent"
    assert rows["m-b"]["enabled"] is True


def test_a_model_id_that_became_an_alias_is_reconciled_as_absent_without_a_duplicate(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed: list[dict[str, Any]] = [
        {"id": "m-canonical", "object": "model", "aliases": []},
        {"id": "local", "object": "model", "aliases": []},
    ]

    def models(_record: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {"object": "list", "data": listed}

    with json_route_server({("GET", "/v1/models"): models}) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        client.post(
            "/api/v1/provider-accounts/from-catalog",
            headers=headers,
            json={
                "providerId": "local_openai_compatible",
                "instanceId": "alias-rec",
                "baseUrl": server.base_url,
                "enabled": True,
            },
        )
        first = client.post("/api/v1/provider-accounts/alias-rec/sync-models", headers=headers)
        listed[:] = [{"id": "m-canonical", "object": "model", "aliases": ["local"]}]
        second = client.post("/api/v1/provider-accounts/alias-rec/sync-models", headers=headers)
        rows = _rows(store, "alias-rec")
    assert (first.status_code, second.status_code) == (200, 200), second.text
    assert {item["model"] for item in second.json()["models"]} == {"m-canonical"}
    assert sorted(rows) == ["local", "m-canonical"]
    assert rows["local"]["enabled"] is False
    assert rows["local"]["source"] == "endpoint_absent"
    assert rows["m-canonical"]["enabled"] is True


def test_an_empty_listing_never_disables_models(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "reconcile.sqlite")) as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.upsert_model({"providerId": "gen-empty", "model": "m-a", "enabled": True})
        absent = reconcile_absent_models(store, "gen-empty", [])
        row = store.list_models("gen-empty")[0]
    assert absent == []
    assert row["enabled"] is True
