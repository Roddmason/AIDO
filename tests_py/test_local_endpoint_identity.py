"""Alta local desde el catálogo: URL del operador preservada y runtime por instancia completo.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store

FROM_CATALOG = "/api/v1/provider-accounts/from-catalog"


def test_resaving_a_local_preset_keeps_the_operator_base_url(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers, _store = client_with_store(tmp_path, monkeypatch)
    first = client.post(
        FROM_CATALOG,
        headers=headers,
        json={"providerId": "llama_cpp", "baseUrl": "http://127.0.0.1:18090/v1", "enabled": True},
    )
    second = client.post(FROM_CATALOG, headers=headers, json={"providerId": "llama_cpp", "enabled": True})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["provider"]["baseUrl"] == "http://127.0.0.1:18090/v1"
    assert second.json()["provider"]["providerCatalogId"] == "llama_cpp"


def test_local_presets_get_their_own_runtime_records(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers, store = client_with_store(tmp_path, monkeypatch)
    response = client.post(
        FROM_CATALOG,
        headers=headers,
        json={
            "providerId": "lm_studio",
            "instanceId": "lms-inst",
            "baseUrl": "http://127.0.0.1:18092/v1",
            "enabled": True,
        },
    )
    installation = store.connection.execute(
        "SELECT kind, enabled, configuration_source FROM runtime_installations WHERE runtime_id = 'lms-inst'"
    ).fetchone()
    runtime_account = store.connection.execute(
        "SELECT auth_mode, configuration_source FROM runtime_accounts WHERE runtime_id = 'lms-inst'"
    ).fetchone()
    capability = store.connection.execute(
        "SELECT enabled FROM runtime_capabilities WHERE runtime = 'lms-inst' AND capability = 'chat'"
    ).fetchone()
    assert response.status_code == 201, response.text
    assert tuple(installation) == ("local", 1, "local_runtime_catalog")
    assert tuple(runtime_account) == ("none", "local_runtime_catalog")
    assert capability["enabled"] == 1
