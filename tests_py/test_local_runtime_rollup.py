"""Rollup de runtimes y candidatos del equipo con endpoints locales y sus modelos cargados.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents import local_model_state
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.local_runtimes import endpoints
from local_control_center.runtime_team import candidates
from local_control_center.runtime_team.roles import RuntimeFacts
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import json_route_server, lm_studio_routes
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


class _UnknownLoadStates:
    def get(self, account, *, max_wait_s: float = 1.0) -> dict[str, str]:
        return {}


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def test_runtime_providers_rollup_lists_local_endpoints_and_keeps_ollama(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(endpoints, "VIEW_LOAD_STATE_WAIT_S", 5.0)
    with json_route_server(lm_studio_routes(models=["qwen3-8b"], loaded={"qwen3-8b"})) as server:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/local-endpoints",
            headers=headers,
            json={"catalogId": "lm_studio", "instanceId": "lms-roll", "baseUrl": server.base_url},
        )
        assert created.status_code == 201, created.text
        response = client.get("/api/v1/runtime/providers")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "ollama" in body
    local = next(item for item in body["local"] if item["id"] == "lms-roll")
    assert local["catalogId"] == "lm_studio"
    assert local["loadedModels"] == ["qwen3-8b"]


def test_team_candidates_report_loaded_models_of_local_runtimes(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(endpoints, "VIEW_LOAD_STATE_WAIT_S", 5.0)
    with json_route_server(lm_studio_routes(models=["qwen3-8b"], loaded={"qwen3-8b"})) as server:
        client, headers, _store = client_with_store(tmp_path, monkeypatch)
        created = client.post(
            "/api/v1/local-endpoints",
            headers=headers,
            json={"catalogId": "lm_studio", "instanceId": "lms-team", "baseUrl": server.base_url},
        )
        assert created.status_code == 201, created.text
        response = client.get("/api/v1/runtime/team-candidates")
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {item["providerId"]: item for item in body["candidates"]}
    assert by_id["lms-team"]["loadedModels"] == ["qwen3-8b"]
    assert by_id["lms-team"]["kind"] == "local"
    assert body["suggestedRoleModels"] == {}


def test_a_failed_model_keeps_a_local_runtime_with_another_validated_model_in_the_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(local_model_state, "LOAD_STATE_CACHE", _UnknownLoadStates())
    facts = {"llama-team": RuntimeFacts("llama-team", "llama.cpp", "local", ("product_owner", "architect"))}
    monkeypatch.setattr(candidates, "load_runtime_facts", lambda _connection, *, project_id: facts)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        store.upsert_provider_account(
            {
                "providerId": "llama-team",
                "displayName": "llama.cpp",
                "providerType": "local",
                "apiFormat": "openai_compatible",
                "providerFamily": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        store.set_provider_catalog_id("llama-team", "llama_cpp")
        for model in ("gemma-a", "qwen-b"):
            store.upsert_model(
                {"providerId": "llama-team", "model": model, "enabled": True, "source": "test"}
            )
        settings = LocalModelSettingsRepository(connection)
        settings.upsert("llama-team", "gemma-a", actor="operator", is_default=True)
        record_model_execution(connection, "llama-team", "gemma-a", True, "test_prompt", started_at=_ago(3))
        record_model_execution(connection, "llama-team", "qwen-b", False, "test_prompt", started_at=_ago(1))
        service = candidates.RuntimeTeamCandidatesService(connection)
        body = service.list_candidates(project_id=None, selected=None)
    assert body["suggestedRoleRuntimes"].get("product_owner") == "llama-team"
    assert body["candidates"][0]["validation"]["model"] == "gemma-a"
    assert body["suggestedRoleModels"] == {"product_owner": "gemma-a", "architect": "gemma-a"}


def test_team_candidates_preview_role_models_with_the_sealing_resolver(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, str]] = []

    def sealing_resolver(_connection, role_runtimes: Mapping[str, str]) -> dict[str, str]:
        calls.append(dict(role_runtimes))
        return {"product_owner": "qwen3-8b"}

    monkeypatch.setattr(candidates, "resolve_team_role_models", sealing_resolver)
    client, _headers, _store = client_with_store(tmp_path, monkeypatch)
    response = client.get("/api/v1/runtime/team-candidates")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestedRoleModels"] == {"product_owner": "qwen3-8b"}
    assert calls == [{role: pid for role, pid in body["suggestedRoleRuntimes"].items() if pid}]
