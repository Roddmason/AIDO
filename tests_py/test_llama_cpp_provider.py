"""llama.cpp (llama-server) entra al catálogo como runtime local OpenAI-compatible.

Familia ``openai_compatible`` para quedar en ``MODEL_RUNTIME_TOOLS`` (PO y Developer por patch sin
adaptadores nuevos) y siembra de ``code_edit``/``code_review`` como OmniRoute, o los roles de build
y review la descartan por capacidades faltantes.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.shared.db import open_sqlite_connection
from tests_py.test_provider_setup_catalog import auth_headers, catalog_by_id, create_client

LLAMA_CPP_BASE_URL = "http://127.0.0.1:8082/v1"


def test_catalog_exposes_llama_cpp_as_a_local_openai_compatible_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = catalog_by_id(create_client(tmp_path, monkeypatch))["llama_cpp"]
    assert entry["providerType"] == "local"
    assert entry["apiFormat"] == "openai_compatible"
    assert entry["providerFamily"] == "openai_compatible"
    assert entry["defaultBaseUrl"] == LLAMA_CPP_BASE_URL
    assert entry["credentialKind"] == "optional_bearer_token"
    assert entry["requiredFields"] == []


def test_from_catalog_llama_cpp_seeds_build_and_review_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=auth_headers(client),
        json={"providerId": "llama_cpp", "enabled": True},
    )
    assert response.status_code == 201, response.text
    provider = response.json()["provider"]
    assert provider["providerId"] == "llama_cpp"
    assert provider["baseUrl"] == LLAMA_CPP_BASE_URL
    with (
        closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))) as connection,
        connection,
    ):
        rows = connection.execute(
            "SELECT capability FROM runtime_capabilities WHERE runtime = 'llama_cpp' AND enabled = 1"
        ).fetchall()
        instance = provider_instance("llama_cpp", connection=connection)
    assert {str(row["capability"]) for row in rows} == {"chat", "code_edit", "code_review"}
    assert isinstance(instance, OpenAICompatibleProvider)
    assert instance.base_url == LLAMA_CPP_BASE_URL
