"""El sync de una cuenta de inferencia self-hosted deja costo cero sin pisar el precio del operador.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.provider_catalog_api import self_hosted_model_pricing
from local_control_center.app import create_app
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.execution_client import CompletedExecutionClient as TestClient
from tests_py.fakes.local_llm_servers import LLAMA_ROUTER_MODELS, running_llama_router

ROUTER_MODEL_IDS = {str(item["id"]) for item in LLAMA_ROUTER_MODELS}
OVERRIDDEN_MODEL = "qwen3.8-27b"


@pytest.fixture
def llama_router():
    with running_llama_router() as (root_url, _state):
        yield root_url


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    with ExitStack() as stack:
        store = stack.enter_context(
            closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
        )
        store.init()
        client = stack.enter_context(TestClient(create_app(runtime=store, static_dir=None)))
        token = client.get("/api/v1/security/handshake").json()["token"]
        yield client, {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def _models(response) -> dict[str, dict[str, Any]]:
    assert response.status_code == 200, response.text
    return {item["model"]: item for item in response.json()["models"]}


def test_self_hosted_pricing_is_zero_unless_the_operator_set_a_price():
    discovered = {"model": "m", "inputPricePerMtok": None, "outputPricePerMtok": None}
    fresh = self_hosted_model_pricing(discovered, None)
    assert fresh["freeTier"] is True
    assert (fresh["inputPricePerMtok"], fresh["outputPricePerMtok"], fresh["reasoningPricePerMtok"]) == (
        0.0,
        0.0,
        0.0,
    )
    synced_before = {"source": "provider_account_sync:llama_cpp", "inputPricePerMtok": 3.0, "freeTier": False}
    assert self_hosted_model_pricing(discovered, synced_before)["inputPricePerMtok"] == 0.0
    override = {
        "source": "operator_override",
        "inputPricePerMtok": 0.5,
        "cachedInputPricePerMtok": None,
        "outputPricePerMtok": 1.5,
        "reasoningPricePerMtok": None,
        "freeTier": False,
        "freeTierNotes": "",
    }
    kept = self_hosted_model_pricing(discovered, override)
    assert (kept["inputPricePerMtok"], kept["outputPricePerMtok"], kept["freeTier"]) == (0.5, 1.5, False)


def test_sync_makes_self_hosted_models_free_and_keeps_an_operator_price(api, llama_router):
    client, headers = api
    created = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=headers,
        json={"providerId": "llama_cpp", "enabled": True},
    )
    assert created.status_code == 201, created.text
    patched = client.patch(
        "/api/v1/model-gateway/providers/llama_cpp", headers=headers, json={"baseUrl": f"{llama_router}/v1"}
    )
    assert patched.status_code == 200, patched.text
    synced = _models(client.post("/api/v1/provider-accounts/llama_cpp/sync-models", headers=headers))
    assert set(synced) == ROUTER_MODEL_IDS
    for item in synced.values():
        assert item["freeTier"] is True
        assert (item["inputPricePerMtok"], item["outputPricePerMtok"]) == (0.0, 0.0)
    override = client.patch(
        f"/api/v1/model-gateway/models/llama_cpp:{OVERRIDDEN_MODEL}",
        headers=headers,
        json={"inputPricePerMtok": 0.5, "outputPricePerMtok": 1.5, "freeTier": False},
    )
    assert override.status_code == 200, override.text
    resynced = _models(client.post("/api/v1/provider-accounts/llama_cpp/sync-models", headers=headers))
    overridden = resynced[OVERRIDDEN_MODEL]
    assert (overridden["inputPricePerMtok"], overridden["outputPricePerMtok"], overridden["freeTier"]) == (
        0.5,
        1.5,
        False,
    )
    assert resynced["gemma-4-26b-a4b"]["freeTier"] is True
