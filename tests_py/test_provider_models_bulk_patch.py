"""Selección masiva de modelos por proveedor: ``PATCH /api/v1/model-gateway/providers/{id}/models``.

Un gateway como OmniRoute anuncia más de mil modelos; guardar la selección con un PATCH por fila deja
al operador esperando sin feedback. Estas pruebas fijan que el endpoint masivo aplica el flag en una
sola transacción (todo o nada), marca las filas como elección del operador para que un resync las
respete, exige el token de escritura y que el sync de un gateway informa su allowlist recomendada.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.agents.providers.base import ModelInfo
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from tests_py.test_gateway_runtime_validation import (
    ALLOWLISTED,
    UNAVAILABLE_UPSTREAM,
    _create_omniroute,
    _omniroute_server,
)
from tests_py.test_provider_setup_catalog import auth_headers, create_client, enable_remote_provider

PROVIDER = "openai_compatible"
SYNC_URL = f"/api/v1/provider-accounts/{PROVIDER}/sync-models"
BULK_URL = f"/api/v1/model-gateway/providers/{PROVIDER}/models"
MODEL_COUNT = 1200


def _synced_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Control plane con un proveedor OpenAI-compatible cuyo catálogo tiene ``MODEL_COUNT`` modelos."""
    monkeypatch.setenv("AIDO_BULK_MODELS_KEY", "test-bulk-models-token-123456")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client,
        headers,
        PROVIDER,
        base_url="https://provider.example.invalid/v1",
        credential_ref="env:AIDO_BULK_MODELS_KEY",
    )
    discovered = [
        ModelInfo(providerId=PROVIDER, model=f"vendor/model-{index:04d}", displayName=f"Model {index}")
        for index in range(MODEL_COUNT)
    ]
    monkeypatch.setattr(OpenAICompatibleProvider, "list_models", lambda _self: discovered)
    synced = client.post(SYNC_URL, headers=headers)
    assert synced.status_code == 200, synced.text
    assert len(synced.json()["models"]) == MODEL_COUNT
    # Sin allowlist curada el proveedor no ofrece recomendados.
    assert synced.json()["recommendedModels"] is None
    return client, headers


def _enabled_by_model(client) -> dict[str, bool]:
    rows = client.get("/api/v1/model-gateway/models").json()["models"]
    return {str(row["model"]): bool(row["enabled"]) for row in rows if row["providerId"] == PROVIDER}


def _synced_enabled(client) -> dict[str, bool]:
    """Solo las filas del catálogo sincronizado (el control plane siembra además un modelo configurado)."""
    return {
        model: enabled for model, enabled in _enabled_by_model(client).items() if model.startswith("vendor/")
    }


def test_bulk_patch_disables_the_whole_catalog_and_survives_a_resync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = _synced_client(tmp_path, monkeypatch)
    catalog_size = len(_enabled_by_model(client))

    response = client.patch(BULK_URL, json={"enabled": False}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"providerId": PROVIDER, "enabled": False, "updated": catalog_size}
    assert not any(_enabled_by_model(client).values())
    # La elección del operador queda como override: resincronizar no vuelve a habilitar nada.
    assert client.post(SYNC_URL, headers=headers).status_code == 200
    assert not any(_enabled_by_model(client).values())


def test_bulk_patch_applies_only_the_listed_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers = _synced_client(tmp_path, monkeypatch)
    assert client.patch(BULK_URL, json={"enabled": False}, headers=headers).status_code == 200
    # Más ids que un tramo de SQLite (500) para cubrir el envío en tramos; un id repetido cuenta una vez.
    chosen = [f"{PROVIDER}:vendor/model-{index:04d}" for index in range(0, 1100)]

    response = client.patch(BULK_URL, json={"enabled": True, "models": [*chosen, chosen[0]]}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["updated"] == len(chosen)
    enabled = _synced_enabled(client)
    assert sum(enabled.values()) == len(chosen)
    assert enabled["vendor/model-1099"] is True
    assert enabled["vendor/model-1100"] is False


def test_bulk_patch_is_all_or_nothing_for_unknown_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = _synced_client(tmp_path, monkeypatch)
    before = _enabled_by_model(client)

    response = client.patch(
        BULK_URL,
        json={"enabled": False, "models": [f"{PROVIDER}:vendor/model-0000", "other:not-this-provider"]},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "models_not_found"
    assert _enabled_by_model(client) == before


def test_bulk_patch_requires_the_write_token_and_a_known_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers = _synced_client(tmp_path, monkeypatch)

    unauthorized = client.patch(BULK_URL, json={"enabled": False})
    assert unauthorized.status_code in {401, 403}
    assert all(_synced_enabled(client).values())

    missing = client.patch(
        "/api/v1/model-gateway/providers/not-a-provider/models", json={"enabled": False}, headers=headers
    )
    assert missing.status_code == 404

    invalid = client.patch(BULK_URL, json={"enabled": "maybe"}, headers=headers)
    assert invalid.status_code == 422


def test_gateway_sync_reports_its_curated_allowlist_as_recommended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _omniroute_server() as base_url:
        _create_omniroute(client, headers, base_url)
        synced = client.post("/api/v1/provider-accounts/omniroute/sync-models", headers=headers)
    assert synced.status_code == 200, synced.text
    assert sorted(synced.json()["recommendedModels"]) == sorted(ALLOWLISTED)
    assert UNAVAILABLE_UPSTREAM not in synced.json()["recommendedModels"]
