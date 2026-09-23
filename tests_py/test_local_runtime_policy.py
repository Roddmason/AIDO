"""La habilitación de runtimes locales usa su propio interruptor y el modo de proyecto `local`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import get_args

import pytest

from local_control_center.agents.contracts import RuntimeMode
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_readiness import apply_effective_readiness
from local_control_center.agents.runtime_status import RUNTIME_MODES
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now

LOOPBACK_URL = "http://127.0.0.1:1/v1"
LAN_URL = "http://192.168.1.50:8082/v1"


@contextmanager
def _connection(tmp_path: Path) -> Iterator:
    with closing(open_sqlite_connection(tmp_path / "policy.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def _local_account(store: ProviderAccountStore, provider_id: str, base_url: str) -> dict:
    store.upsert_provider_account(
        {
            "providerId": provider_id,
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "baseUrl": base_url,
            "enabled": True,
        }
    )
    return store.set_provider_catalog_id(provider_id, "llama_cpp")


def test_local_runtime_settings_are_registered_with_a_bounded_call_budget():
    enabled = descriptor_for("runtime.local.enabled")
    budget = descriptor_for("runtime.local.maxCallSeconds")
    assert enabled is not None and enabled.type == "boolean" and enabled.default is True
    assert budget is not None and (budget.default, budget.minimum, budget.maximum) == (900, 30, 3600)
    assert "local" in descriptor_for("project.runtime.defaultMode").enum
    with pytest.raises(ValueError):
        validate_value(budget, 10)
    assert "local" in get_args(RuntimeMode)
    assert "local" in RUNTIME_MODES


def test_phase78_seeds_the_local_switch_from_the_operator_ollama_switch(tmp_path):
    with _connection(tmp_path) as connection:
        settings = SettingsRepository(connection)
        settings.set_value("runtime.ollama.enabled", "general", None, False)
        settings.clear_value("runtime.local.enabled", "general", None)
        connection.execute("DELETE FROM schema_migrations WHERE version = 78")
        initialize_platform_schema(connection)
        assert settings.get_value("runtime.local.enabled", "general", None) is False


def test_local_accounts_use_the_local_switch_and_lan_accounts_the_remote_one(tmp_path):
    with _connection(tmp_path) as connection:
        store = ProviderAccountStore(connection)
        loopback = _local_account(store, "llama_cpp", LOOPBACK_URL)
        lan = _local_account(store, "lan-llama", LAN_URL)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.ollama.enabled", False)
        assert repo.runtime_policy_decision(provider_id="llama_cpp", kind="local", account=loopback)[
            "allowed"
        ]
        repo.set_runtime_setting("runtime.local.enabled", False)
        denied = repo.runtime_policy_decision(provider_id="llama_cpp", kind="local", account=loopback)
        assert denied["allowed"] is False
        assert denied["reason"] == "runtime.local.enabled is false."
        assert repo.runtime_policy_decision(provider_id="lan-llama", kind="local", account=lan)["allowed"]
        repo.set_runtime_setting("runtime.remote.enabled", False)
        remote_denied = repo.runtime_policy_decision(provider_id="lan-llama", kind="local")
        assert remote_denied["reason"] == "runtime.remote.enabled is false."
        assert repo.runtime_execution_policy()["global"]["localEnabled"] is False


def test_ollama_is_the_local_switch_restricted_to_ollama(tmp_path):
    with _connection(tmp_path) as connection:
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting("runtime.local.enabled", False)
        assert (
            repo.runtime_policy_decision(provider_id="ollama", kind="local")["reason"]
            == "runtime.local.enabled is false."
        )
        repo.set_runtime_setting("runtime.local.enabled", True)
        repo.set_runtime_setting("runtime.ollama.enabled", False)
        assert (
            repo.runtime_policy_decision(provider_id="ollama", kind="local")["reason"]
            == "runtime.ollama.enabled is false."
        )


def test_project_local_mode_admits_only_verified_local_model_runtimes(tmp_path):
    with _connection(tmp_path) as connection:
        store = ProviderAccountStore(connection)
        _local_account(store, "llama_cpp", LOOPBACK_URL)
        _local_account(store, "lan-llama", LAN_URL)
        repo = RuntimeConfigRepository(connection)
        repo.set_runtime_setting(
            "project.runtime.defaultMode", "local", scope="project", scope_id="project-local"
        )
        for provider in ("llama_cpp", "ollama"):
            decision = repo.runtime_policy_decision(
                provider_id=provider, kind="local", project_id="project-local"
            )
            assert decision["allowed"], decision["reason"]
        for provider, kind in (("lan-llama", "local"), ("codex_cli", "cli"), ("openai_api", "api")):
            decision = repo.runtime_policy_decision(
                provider_id=provider, kind=kind, project_id="project-local"
            )
            assert decision["reason"] == f"project.runtime.defaultMode=local blocks provider {provider}."


def test_readiness_reads_the_local_switch_and_ignores_the_project_remote_veto(tmp_path):
    with _connection(tmp_path) as connection:
        ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())
        status = {
            "id": "llama_cpp",
            "kind": "local",
            "configured": True,
            "installed": True,
            "authenticated": True,
            "executable": True,
            "reason": "Ready",
            "healthStatus": "healthy",
            "healthCheckedAt": utc_now(),
        }
        account = {
            "providerId": "llama_cpp",
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "providerCatalogId": "llama_cpp",
            "baseUrl": LOOPBACK_URL,
        }
        enabled = apply_effective_readiness(
            connection,
            dict(status),
            account,
            {
                "allowed": True,
                "policy": {
                    "global": {"localEnabled": True, "remoteEnabled": False},
                    "project": {"remoteEnabled": False},
                },
            },
        )
        disabled = apply_effective_readiness(
            connection,
            dict(status),
            account,
            {
                "allowed": True,
                "policy": {
                    "global": {"localEnabled": False, "remoteEnabled": True},
                    "project": {"remoteEnabled": True},
                },
            },
        )
    assert enabled["globallyEnabled"] and enabled["projectEnabled"] and enabled["executable"]
    assert "globally_disabled" in disabled["blockingReasons"]
    assert disabled["executable"] is False


def test_agent_profiles_accept_the_local_runtime_mode(tmp_path):
    with _connection(tmp_path) as connection:
        profile = AgentsRepository(connection).upsert_agent_profile(
            {"id": "local-profile", "name": "Local", "role": "developer", "runtimeMode": "local"}
        )
    assert profile["runtimeMode"] == "local"
