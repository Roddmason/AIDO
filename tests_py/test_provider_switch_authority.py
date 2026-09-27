"""Pruebas del switch por proveedor (``provider_accounts.enabled``) como autoridad única de ejecución.

Un proveedor apagado —cualquier kind, en particular un CLI como Claude Code— no es ejecutable en el
estado de runtimes, no lo elige la readiness de ningún agente, el ResourceManager lo rechaza en duro y el
TeamScheduler no lo propone. La migración 84 enciende una única vez los CLI que ya corrían para que nada
que funciona hoy deje de funcionar, y Ollama se puede apagar y borrar sin que una semilla lo resucite.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.developer_agent_contract import developer_agent_readiness
from local_control_center.agents.product_owner_agent_contract import is_product_owner_runtime
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import (
    PROVIDER_DISABLED_REASON,
    RuntimeStatusService,
    _apply_provider_switch,
    _cli_provider_status,
)
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.local_runtimes.endpoints import (
    LocalEndpointInUseError,
    delete_local_endpoint_account,
    endpoint_references,
)
from local_control_center.ollama import api as ollama_api
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.team_scheduler.scheduler import schedule_team

CLI_IDS = ("codex_cli", "claude_code_cli", "openhands", "swe_agent")


def _connection(tmp_path: Path):
    connection = open_sqlite_connection(tmp_path / "platform.sqlite")
    initialize_platform_schema(connection)
    return connection


def _cli_status(account: dict) -> dict:
    """Status de un CLI detectado, autenticado, con policy y capacidades: solo varía el switch."""
    status = _cli_provider_status(
        account,
        {"enabled": True, "detectedVersion": "1.0.0"},
        {
            "enabled": True,
            "healthStatus": "healthy",
            "lastValidationAt": datetime.now(UTC).isoformat(),
        },
        {
            "status": "installed",
            "version": "1.0.0",
            "executable": "claude" if "claude" in account["providerId"] else "codex",
        },
        ["chat", "code_edit"],
        {"allowed": True},
    )
    return _apply_provider_switch(status, account)


def _account(provider_id: str, *, enabled: bool) -> dict:
    return {"providerId": provider_id, "displayName": provider_id, "providerType": "cli", "enabled": enabled}


def test_a_disabled_cli_is_not_executable_and_says_why() -> None:
    enabled = _cli_status(_account("claude_code_cli", enabled=True))
    disabled = _cli_status(_account("claude_code_cli", enabled=False))

    assert enabled["executable"] is True
    assert disabled["executable"] is False
    assert disabled["canRunPrompt"] is False
    assert disabled["canEditWorkspace"] is False
    assert disabled["productOwnerExecutable"] is False
    assert disabled["enabled"] is False
    assert disabled["reason"] == PROVIDER_DISABLED_REASON
    # Apagarlo es una decisión del operador, no un runtime roto que reparar.
    assert disabled["blockerType"] is None


def test_readiness_never_picks_a_cli_the_operator_switched_off() -> None:
    codex = _cli_status(_account("codex_cli", enabled=True))
    claude_off = _cli_status(_account("claude_code_cli", enabled=False))
    claude_on = _cli_status(_account("claude_code_cli", enabled=True))

    # Con ambos encendidos manda el orden del contrato; apagando Claude se elige Codex.
    assert developer_agent_readiness([claude_on])["selectedRuntimeId"] == "claude_code_cli"
    readiness = developer_agent_readiness([claude_off, codex])
    assert readiness["selectedRuntimeId"] == "codex_cli"
    assert readiness["candidateRuntimeIds"] == ["codex_cli"]
    only_disabled = developer_agent_readiness([claude_off])
    assert only_disabled["executable"] is False
    # Ni pedido explícitamente: el runtime preferido apagado no se ejecuta.
    pinned = developer_agent_readiness([claude_off, codex], preferred_runtime="claude_code_cli")
    assert pinned["executable"] is False
    assert pinned["reason"].startswith("provider_disabled")
    # Aunque un status viejo lo diga ejecutable, el switch explícito gana.
    assert is_product_owner_runtime({**claude_on, "enabled": False}) is False


def test_runtime_status_service_applies_the_switch_to_every_kind(tmp_path: Path) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        store = ProviderAccountStore(connection)
        store.patch_provider_account("anthropic_api", {"enabled": False})
        statuses = {item["id"]: item for item in RuntimeStatusService(connection).list_provider_statuses()}

    assert statuses["anthropic_api"]["enabled"] is False
    assert statuses["anthropic_api"]["executable"] is False
    assert "provider_disabled" in statuses["anthropic_api"]["blockingReasons"]
    for provider_id in CLI_IDS:
        assert statuses[provider_id]["enabled"] is False
        assert statuses[provider_id]["executable"] is False


def _executable_cli_statuses(_service: RuntimeStatusService, *, project_id: str | None = None) -> list[dict]:
    del project_id
    return [
        _cli_status(_account("claude_code_cli", enabled=True)),
        _cli_status(_account("codex_cli", enabled=True)),
    ]


def test_resource_manager_hard_rejects_a_disabled_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El status (inyectado) dice ejecutable: el rechazo sale del switch persistido, no del status.
    monkeypatch.setattr(RuntimeStatusService, "list_provider_statuses", _executable_cli_statuses)
    with closing(_connection(tmp_path)) as connection, connection:
        ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())
        connection.execute("UPDATE model_catalog SET enabled = 0")
        connection.execute(
            "UPDATE model_catalog SET enabled = 1 WHERE provider_id IN ('claude_code_cli', 'codex_cli')"
        )
        store = ProviderAccountStore(connection)
        store.patch_provider_account("codex_cli", {"enabled": True})
        store.patch_provider_account("claude_code_cli", {"enabled": False})

        decision = AIResourceManager(connection).select_resource(
            AIResourceRequest(
                task_type="developer.implementation",
                required_capabilities=["chat"],
                allow_unknown_cost=True,
            ),
            record=False,
        )

    assert decision["selected"]["providerId"] == "codex_cli"
    rejected = {(item["providerId"], item["reason"]) for item in decision["rejected"]}
    assert ("claude_code_cli", "provider_disabled") in {(provider, reason) for provider, reason in rejected}
    assert all(item["providerId"] != "claude_code_cli" for item in decision["candidates"])


def test_team_scheduler_drops_disabled_providers() -> None:
    plan = schedule_team(
        scope=["backend"], risk="high", mode="critical", disabled_providers={"claude_code_cli"}
    )
    default = schedule_team(scope=["backend"], risk="high", mode="critical")

    for role in plan["roles"]:
        assert "claude_code_cli" not in role["providerPreference"]
        assert role["runtime"] != "claude_code_cli"
    assert any(role["runtime"] == "claude_code_cli" for role in default["roles"])


def test_switching_a_cli_on_is_enough_to_let_it_run(tmp_path: Path) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        store = ProviderAccountStore(connection)
        store.patch_provider_account("claude_code_cli", {"enabled": True})
        installation = connection.execute(
            "SELECT enabled FROM runtime_installations WHERE runtime_id = 'claude_code_cli'"
        ).fetchone()
        store.patch_provider_account("claude_code_cli", {"enabled": False})
        off = connection.execute(
            "SELECT enabled FROM runtime_installations WHERE runtime_id = 'claude_code_cli'"
        ).fetchone()

    assert installation["enabled"] == 1
    assert off["enabled"] == 0


def _rewind_to_phase_82(connection) -> None:
    """Simula una base anterior a la fase 84 (el switch de CLI aún no era autoritativo)."""
    connection.execute("DELETE FROM schema_migrations WHERE version = 84")
    connection.execute("DROP TABLE provider_account_tombstones")
    connection.execute("UPDATE provider_accounts SET enabled = 0 WHERE provider_type = 'cli'")


def test_phase_84_switches_on_exactly_the_clis_that_run_today_and_only_once(tmp_path: Path) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        _rewind_to_phase_82(connection)
        # Claude corre hoy (instalación y cuenta nativa habilitadas); Codex no tiene instalación habilitada.
        connection.execute(
            "UPDATE runtime_installations SET enabled = 1 WHERE runtime_id = 'claude_code_cli'"
        )
        connection.execute("UPDATE runtime_accounts SET enabled = 1 WHERE runtime_id = 'claude_code_cli'")
        connection.execute("UPDATE runtime_installations SET enabled = 0 WHERE runtime_id = 'codex_cli'")

        initialize_platform_schema(connection)
        store = ProviderAccountStore(connection)
        assert store.get_provider_account("claude_code_cli")["enabled"] is True
        assert store.get_provider_account("codex_cli")["enabled"] is False
        assert store.get_provider_account("openhands")["enabled"] is False

        # Desde aquí el switch manda: apagarlo sobrevive a reinicios y a una nueva pasada completa.
        store.patch_provider_account("claude_code_cli", {"enabled": False})
        connection.execute(
            "UPDATE runtime_installations SET enabled = 1 WHERE runtime_id = 'claude_code_cli'"
        )
        connection.execute("UPDATE runtime_accounts SET enabled = 1 WHERE runtime_id = 'claude_code_cli'")
        initialize_platform_schema(connection)
        connection.execute("DELETE FROM schema_migrations WHERE version = 82")
        initialize_platform_schema(connection)
        assert store.get_provider_account("claude_code_cli")["enabled"] is False
        applied = connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 84").fetchone()[
            0
        ]

    assert applied == 1


def test_ollama_can_be_disabled_without_deleting_it(tmp_path: Path) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        store = ProviderAccountStore(connection)
        store.patch_provider_account("ollama", {"enabled": True})
        store.patch_provider_account("ollama", {"enabled": False})
        connection.execute("DELETE FROM schema_migrations WHERE version = 82")
        initialize_platform_schema(connection)
        statuses = {item["id"]: item for item in RuntimeStatusService(connection).list_provider_statuses()}
        summary = RuntimeStatusService(connection).runtime_provider_status()

    assert statuses["ollama"]["enabled"] is False
    assert statuses["ollama"]["executable"] is False
    assert summary["ollama"]["available"] is False


def test_deleting_ollama_removes_seed_references_and_stays_deleted_after_a_schema_upgrade(
    tmp_path: Path,
) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        store = ProviderAccountStore(connection)
        # El placeholder sembrado no es una referencia del operador: no bloquea el borrado.
        assert endpoint_references(connection, "ollama") == []

        delete_local_endpoint_account(connection, store.get_provider_account("ollama"))

        policies = connection.execute(
            "SELECT preferred_json, fallback_json, escalation_json FROM role_model_policies"
        ).fetchall()
        assert all("local_default" not in "".join(row) for row in policies)
        # Actualización de esquema simulada: una pasada completa re-ejecuta las semillas.
        connection.execute("DELETE FROM schema_migrations WHERE version = 82")
        initialize_platform_schema(connection)
        assert "ollama" not in {account["providerId"] for account in store.list_provider_accounts()}
        assert (
            connection.execute("SELECT 1 FROM model_catalog WHERE provider_id = 'ollama'").fetchone() is None
        )
        assert (
            connection.execute("SELECT 1 FROM runtime_installations WHERE runtime_id = 'ollama'").fetchone()
            is None
        )

        # Volver a darlo de alta es una decisión explícita: retira el tombstone y sobrevive a migraciones.
        store.upsert_provider_account(
            {
                "providerId": "ollama",
                "displayName": "Ollama",
                "providerType": "local",
                "apiFormat": "ollama",
                "providerFamily": "ollama",
                "baseUrl": "http://127.0.0.1:11434",
                "enabled": True,
            }
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 82")
        initialize_platform_schema(connection)
        assert store.get_provider_account("ollama")["enabled"] is True


def test_operator_authored_ollama_references_still_block_the_delete(tmp_path: Path) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        row = connection.execute("SELECT preferred_json FROM role_model_policies WHERE id = 'qa'").fetchone()
        preferred = [*json_loads(row["preferred_json"], []), {"provider": "ollama", "model": "qwen3:8b"}]
        connection.execute(
            "UPDATE role_model_policies SET preferred_json = ? WHERE id = 'qa'", (json_dumps(preferred),)
        )
        store = ProviderAccountStore(connection)

        with pytest.raises(LocalEndpointInUseError) as blocked:
            delete_local_endpoint_account(connection, store.get_provider_account("ollama"))

        assert [reference["id"] for reference in blocked.value.references] == ["qa"]
        # Nada se escribió: la cuenta y el placeholder sembrado siguen ahí.
        assert store.get_provider_account("ollama")
        refreshed = connection.execute(
            "SELECT preferred_json FROM role_model_policies WHERE id = 'qa'"
        ).fetchone()
        assert "local_default" in refreshed["preferred_json"]


class _Platform:
    def __init__(self, connection) -> None:
        self.connection = connection


def _ollama_client(connection) -> TestClient:
    app = FastAPI()
    app.include_router(
        ollama_api.create_router(platform=_Platform(connection), require_write=lambda _request: None)
    )
    return TestClient(app)


def test_ollama_api_toggles_and_deletes_an_endpoint(tmp_path: Path) -> None:
    with closing(_connection(tmp_path)) as connection, connection:
        client = _ollama_client(connection)

        disabled = client.patch("/api/v1/ollama/endpoints/ollama", json={"enabled": False})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["endpoint"]["enabled"] is False
        runtime_row = connection.execute(
            "SELECT enabled FROM runtime_installations WHERE runtime_id = 'ollama'"
        ).fetchone()
        assert runtime_row["enabled"] == 0

        deleted = client.delete("/api/v1/ollama/endpoints/ollama")
        assert deleted.status_code == 204, deleted.text
        listed = client.get("/api/v1/ollama/endpoints").json()["endpoints"]
        assert all(endpoint["id"] != "ollama" for endpoint in listed)
        assert client.delete("/api/v1/ollama/endpoints/ollama").status_code == 404
