"""Configuración por modelo local: por defecto único, procedencia, capacidades opt-in y fase 80.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.shared.db import immediate_transaction, open_sqlite_connection
from local_control_center.shared.migrations import init_phase80_schema, initialize_platform_schema
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        yield handle


def _local_account(connection, provider_id: str, models: tuple[str, ...] = ()) -> None:
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
    for model in models:
        store.upsert_model({"providerId": provider_id, "model": model, "enabled": True, "source": "test"})


def test_upsert_keeps_unsent_fields_and_records_who_set_each_one(connection):
    repository = LocalModelSettingsRepository(connection)
    repository.upsert("llama_cpp", "gemma-a", actor="operator", code_edit=True, operator_order=2)
    updated = repository.upsert("llama_cpp", "gemma-a", actor="runtime_validation", json_schema=True)
    assert (updated.code_edit, updated.operator_order, updated.json_schema, updated.is_default) == (
        True,
        2,
        True,
        False,
    )
    assert dict(updated.provenance) == {
        "code_edit": "operator",
        "operator_order": "operator",
        "json_schema": "runtime_validation",
    }


def test_marking_a_default_unmarks_the_previous_one_of_the_same_account(connection):
    repository = LocalModelSettingsRepository(connection)
    repository.upsert("llama_cpp", "gemma-a", actor="operator", is_default=True)
    repository.upsert("other", "gemma-a", actor="operator", is_default=True)
    repository.upsert("llama_cpp", "qwen-b", actor="operator", is_default=True)
    assert repository.default_model("llama_cpp") == "qwen-b"
    assert repository.default_model("other") == "gemma-a"
    assert [(item.model, item.is_default) for item in repository.list_for_account("llama_cpp")] == [
        ("gemma-a", False),
        ("qwen-b", True),
    ]


def test_unmarking_the_previous_default_stamps_its_provenance_to_the_new_actor(connection):
    repository = LocalModelSettingsRepository(connection)
    repository.upsert("llama_cpp", "gemma-a", actor="alice", is_default=True)
    repository.upsert("llama_cpp", "qwen-b", actor="bob", is_default=True)
    unmarked = repository.get("llama_cpp", "gemma-a")
    assert unmarked is not None
    assert unmarked.is_default is False
    assert unmarked.provenance["is_default"] == "bob"
    marked = repository.get("llama_cpp", "qwen-b")
    assert marked is not None
    assert marked.provenance["is_default"] == "bob"


def test_the_database_rejects_two_defaults_for_one_account(connection):
    LocalModelSettingsRepository(connection).upsert("llama_cpp", "gemma-a", actor="operator", is_default=True)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO local_model_settings (provider_id, model, is_default, updated_at) "
            "VALUES ('llama_cpp', 'qwen-b', 1, ?)",
            (utc_now(),),
        )


def test_capabilities_are_chat_plus_the_model_opt_ins(connection):
    repository = LocalModelSettingsRepository(connection)
    repository.upsert("llama_cpp", "coder", actor="operator", code_edit=True, code_review=True)
    assert repository.capabilities_for("llama_cpp", "coder") == frozenset(
        {"chat", "code_edit", "code_review"}
    )
    assert repository.capabilities_for("llama_cpp", "never-configured") == frozenset({"chat"})


def test_runtime_capabilities_only_count_enabled_models(connection):
    _local_account(connection, "llama_cpp", ("coder", "reviewer"))
    repository = LocalModelSettingsRepository(connection)
    repository.upsert("llama_cpp", "coder", actor="operator", code_edit=True)
    repository.upsert("llama_cpp", "reviewer", actor="operator", code_review=True)
    assert repository.enabled_capabilities("llama_cpp") == frozenset({"chat", "code_edit", "code_review"})
    connection.execute(
        "UPDATE model_catalog SET enabled = 0 WHERE provider_id = 'llama_cpp' AND model = 'reviewer'"
    )
    assert repository.enabled_capabilities("llama_cpp") == frozenset({"chat", "code_edit"})


def test_json_schema_flag_and_delete_are_scoped_to_the_account(connection):
    repository = LocalModelSettingsRepository(connection)
    repository.upsert("llama_cpp", "gemma-a", actor="runtime_validation", json_schema=True)
    repository.upsert("other", "gemma-a", actor="operator", code_edit=True)
    assert repository.json_schema_enabled("llama_cpp", "gemma-a") is True
    assert repository.json_schema_enabled("other", "gemma-a") is False
    assert repository.delete_for_account("llama_cpp") == 1
    assert repository.list_for_account("llama_cpp") == []
    assert [item.model for item in repository.list_for_account("other")] == ["gemma-a"]


def test_writes_reuse_the_caller_transaction(connection):
    repository = LocalModelSettingsRepository(connection)
    with immediate_transaction(connection):
        repository.upsert("llama_cpp", "gemma-a", actor="operator", is_default=True)
        assert repository.delete_for_account("llama_cpp") == 1
    assert repository.list_for_account("llama_cpp") == []


@pytest.mark.parametrize(
    ("provider_id", "model", "actor", "order"),
    [
        ("", "m", "operator", None),
        ("p", " ", "operator", None),
        ("p", "m", "", None),
        ("p", "m", "operator", -1),
    ],
)
def test_invalid_input_is_rejected(connection, provider_id, model, actor, order):
    with pytest.raises(ValueError):
        LocalModelSettingsRepository(connection).upsert(provider_id, model, actor=actor, operator_order=order)


def test_phase80_retires_runtime_seeded_code_capabilities_of_llama_cpp_accounts(connection):
    _local_account(connection, "llama-lan")
    _local_account(connection, "llama-operator")
    seeded = json_dumps({"source": "provider_catalog_from_catalog"})
    now = utc_now()
    for runtime, capability, metadata in [
        ("llama-lan", "chat", seeded),
        ("llama-lan", "code_edit", seeded),
        ("llama-lan", "code_review", seeded),
        ("llama-operator", "code_edit", "{}"),
        ("omniroute", "code_edit", seeded),
    ]:
        connection.execute(
            """INSERT INTO runtime_capabilities (id, runtime, capability, enabled, metadata, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(runtime, capability) DO UPDATE SET metadata = excluded.metadata""",
            (f"{runtime}:{capability}", runtime, capability, metadata, now, now),
        )
    connection.execute("DELETE FROM schema_migrations WHERE version = 80")
    init_phase80_schema(connection)
    remaining = {
        (row["runtime"], row["capability"])
        for row in connection.execute(
            "SELECT runtime, capability FROM runtime_capabilities "
            "WHERE runtime IN ('llama-lan', 'llama-operator', 'omniroute')"
        )
    }
    assert remaining >= {("llama-lan", "chat"), ("llama-operator", "code_edit"), ("omniroute", "code_edit")}
    assert not remaining & {("llama-lan", "code_edit"), ("llama-lan", "code_review")}
