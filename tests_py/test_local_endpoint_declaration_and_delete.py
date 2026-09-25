"""Declaración local auditada (WSL/Docker) y borrado de endpoints locales con tombstone y 409.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.quota_manager import QuotaManager, QuotaRequest
from local_control_center.local_runtimes.endpoints import endpoint_references
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads.repository import ThreadsRepository
from tests_py.fakes.local_llm_servers import generic_openai_routes, json_route_server
from tests_py.test_ollama_endpoints_api import client_with_store as client_with_store

ENDPOINTS = "/api/v1/local-endpoints"


def _audit_actions(store, target: str) -> list[str]:
    rows = store.connection.execute(
        "SELECT action FROM audit_events WHERE target = ? ORDER BY created_at", (target,)
    ).fetchall()
    return [row["action"] for row in rows]


def _count(store, table: str, column: str, value: str) -> int:
    return store.connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[
        0
    ]


def test_declaring_a_lan_endpoint_local_is_audited_and_revocable(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers, store = client_with_store(tmp_path, monkeypatch)
    created = client.post(
        ENDPOINTS,
        headers=headers,
        json={"catalogId": "vllm", "instanceId": "vllm-wsl", "baseUrl": "http://192.168.50.10:8000/v1"},
    )
    client.patch(f"{ENDPOINTS}/vllm-wsl", headers=headers, json={"enabled": False})
    declared = client.put(f"{ENDPOINTS}/vllm-wsl/declare-local", headers=headers, json={"declared": True})
    account = ProviderAccountStore(store.connection).get_provider_account("vllm-wsl")
    revoked = client.put(f"{ENDPOINTS}/vllm-wsl/declare-local", headers=headers, json={"declared": False})
    anonymous = client.put(f"{ENDPOINTS}/vllm-wsl/declare-local", json={"declared": True})
    assert created.status_code == 201, created.text
    assert created.json()["locality"] == "remote"
    assert declared.status_code == 200, declared.text
    assert (declared.json()["declaredLocal"], declared.json()["locality"]) == (True, "declared_local")
    assert account["localDeclaration"]["host"] == "192.168.50.10"
    assert account["localDeclaration"]["declaredBy"] == "operator"
    assert (revoked.json()["declaredLocal"], revoked.json()["locality"]) == (False, "remote")
    assert anonymous.status_code == 403
    actions = _audit_actions(store, "vllm-wsl")
    assert "local_endpoint.declared_local" in actions
    assert "local_endpoint.declaration_revoked" in actions


def test_a_public_or_named_host_cannot_be_declared_local(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, headers, store = client_with_store(tmp_path, monkeypatch)
    client.post(
        ENDPOINTS,
        headers=headers,
        json={
            "catalogId": "local_openai_compatible",
            "instanceId": "gen-named",
            "baseUrl": "http://llm.example.invalid:8000/v1",
        },
    )
    rejected = client.put(f"{ENDPOINTS}/gen-named/declare-local", headers=headers, json={"declared": True})
    account = ProviderAccountStore(store.connection).get_provider_account("gen-named")
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "local_declaration_host_not_allowed"
    assert account["localDeclaration"] is None
    assert "local_endpoint.declared_local" not in _audit_actions(store, "gen-named")


def test_delete_tombstones_the_endpoint_without_orphans_and_keeps_evidence(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with json_route_server(generic_openai_routes(models=["m-a"])) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        client.post(
            ENDPOINTS,
            headers=headers,
            json={
                "catalogId": "local_openai_compatible",
                "instanceId": "gen-del",
                "baseUrl": server.base_url,
            },
        )
        ProviderAccountStore(store.connection).upsert_model(
            {"providerId": "gen-del", "model": "m-a", "enabled": True}
        )
        LocalModelSettingsRepository(store.connection).upsert(
            "gen-del", "m-a", actor="operator", is_default=True
        )
        record_model_execution(store.connection, "gen-del", "m-a", True, "test_prompt")
        quota = QuotaManager(store.connection)
        quota.acquire(QuotaRequest(provider_id="gen-del", model="m-a", reserved_tokens=1))
        quota.record_rate_limit(provider_id="gen-del", model="m-a", retry_after_seconds=120)
        assert "gen-del" in quota.providers_in_cooldown()
        deleted = client.delete(f"{ENDPOINTS}/gen-del", headers=headers)
        again = client.delete(f"{ENDPOINTS}/gen-del", headers=headers)
        not_local = client.delete(f"{ENDPOINTS}/codex_cli", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert again.status_code == 404
    assert not_local.status_code == 404
    for table, column in (
        ("provider_accounts", "provider_id"),
        ("model_catalog", "provider_id"),
        ("local_model_settings", "provider_id"),
        ("runtime_installations", "runtime_id"),
        ("runtime_accounts", "runtime_id"),
        ("runtime_capabilities", "runtime"),
        ("provider_limits", "provider_id"),
        ("provider_execution_leases", "provider_id"),
    ):
        assert _count(store, table, column, "gen-del") == 0, table
    # Re-adding the same instance id must not inherit the deleted endpoint's cooldown.
    assert "gen-del" not in QuotaManager(store.connection).providers_in_cooldown()
    assert _count(store, "model_execution_health", "provider_id", "gen-del") == 1
    assert "local_endpoint.deleted" in _audit_actions(store, "gen-del")


def test_delete_answers_409_with_thread_team_and_role_policy_references(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with json_route_server(generic_openai_routes(models=["m-a"])) as server:
        client, headers, store = client_with_store(tmp_path, monkeypatch)
        client.post(
            ENDPOINTS,
            headers=headers,
            json={
                "catalogId": "local_openai_compatible",
                "instanceId": "gen-ref",
                "baseUrl": server.base_url,
            },
        )
        project = ProjectsRepository(store.connection).create_project(
            name="refs", path=tmp_path / "refs", template_id="other"
        )
        thread = ThreadsRepository(store.connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Team on gen-ref",
            metadata={
                "runConfiguration": {
                    "allowedRuntimes": ["gen-ref"],
                    "roleRuntimes": {"product_owner": "gen-ref"},
                }
            },
        )
        policy = store.connection.execute(
            "SELECT id, role FROM role_model_policies ORDER BY role LIMIT 1"
        ).fetchone()
        store.connection.execute(
            "UPDATE role_model_policies SET fallback_json = ? WHERE id = ?",
            (json.dumps([{"provider": "gen-ref", "model": "m-a"}]), policy["id"]),
        )
        response = client.delete(f"{ENDPOINTS}/gen-ref", headers=headers)
        still_there = ProviderAccountStore(store.connection).get_provider_account("gen-ref")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "local_endpoint_in_use"
    assert {"kind": "thread_team", "id": thread["id"], "label": "Team on gen-ref"} in detail["references"]
    assert {"kind": "role_policy", "id": policy["id"], "label": policy["role"]} in detail["references"]
    assert still_there["providerId"] == "gen-ref"


def test_legacy_string_role_policy_refs_also_block_the_delete(
    client_with_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, _headers, store = client_with_store(tmp_path, monkeypatch)
    policy = store.connection.execute(
        "SELECT id, role FROM role_model_policies ORDER BY role LIMIT 1"
    ).fetchone()
    store.connection.execute(
        "UPDATE role_model_policies SET preferred_json = ? WHERE id = ?",
        (json.dumps(["gen-legacy"]), policy["id"]),
    )
    assert endpoint_references(store.connection, "gen-legacy") == [
        {"kind": "role_policy", "id": policy["id"], "label": policy["role"]}
    ]
    assert endpoint_references(store.connection, "gen-other") == []
