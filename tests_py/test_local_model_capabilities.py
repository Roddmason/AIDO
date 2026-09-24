"""Capacidades por modelo en cuentas locales: opt-in por modelo, sin siembra por runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.runtime_team.facts import load_runtime_facts
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        ResourceRepository(handle).record_sample(ResourceSnapshot.test_snapshot())
        handle.execute("UPDATE model_catalog SET enabled = 0")
        yield handle


def _local_account(connection, provider_id: str, model: str) -> None:
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": provider_id,
            "displayName": provider_id,
            "providerType": "local",
            "apiFormat": "openai_compatible",
            "providerFamily": "openai_compatible",
            "baseUrl": "http://127.0.0.1:1/v1",
            "enabled": True,
        }
    )
    store.set_provider_catalog_id(provider_id, "llama_cpp")
    store.upsert_model(
        {
            "providerId": provider_id,
            "model": model,
            "enabled": True,
            "freeTier": True,
            "inputPricePerMtok": 0,
            "outputPricePerMtok": 0,
            "source": "test",
        }
    )


def _advertise(monkeypatch: pytest.MonkeyPatch, *provider_ids: str) -> None:
    statuses = [
        {
            "id": provider_id,
            "kind": "local",
            "providerFamily": "openai_compatible",
            "configured": True,
            "available": True,
            "executable": True,
            "capabilities": ["chat"],
            "reason": "Controlled local endpoint.",
        }
        for provider_id in provider_ids
    ]
    monkeypatch.setattr(
        RuntimeStatusService, "list_provider_statuses", lambda _service, *, project_id=None: statuses
    )


def test_code_capability_comes_from_the_model_opt_in_not_the_runtime(connection, monkeypatch):
    _local_account(connection, "llama-plain", "plain-model")
    _local_account(connection, "llama-coder", "coder-model")
    LocalModelSettingsRepository(connection).upsert(
        "llama-coder", "coder-model", actor="operator", code_edit=True
    )
    _advertise(monkeypatch, "llama-plain", "llama-coder")
    decision = AIResourceManager(connection).select_resource(
        AIResourceRequest(
            task_type="backend_engineer.build",
            required_capabilities=["code"],
            allowed_provider_ids=["llama-plain", "llama-coder"],
        ),
        record=False,
    )
    rejected = {item["model"]: item["reason"] for item in decision["rejected"]}
    assert rejected["plain-model"] == "missing_capabilities:code"
    assert "coder-model" not in rejected
    assert decision["selected"]["model"] == "coder-model"


def test_local_runtime_roles_follow_the_enabled_model_opt_ins(connection, monkeypatch):
    _local_account(connection, "llama-coder", "coder-model")
    _advertise(monkeypatch, "llama-coder")
    assert "developer" not in load_runtime_facts(connection, project_id=None)["llama-coder"].eligible_roles
    LocalModelSettingsRepository(connection).upsert(
        "llama-coder", "coder-model", actor="operator", code_edit=True
    )
    assert "developer" in load_runtime_facts(connection, project_id=None)["llama-coder"].eligible_roles
    connection.execute("UPDATE model_catalog SET enabled = 0 WHERE provider_id = 'llama-coder'")
    assert "developer" not in load_runtime_facts(connection, project_id=None)["llama-coder"].eligible_roles
