"""La validación del equipo se evalúa por (provider, modelo): la falla de un modelo no invalida otro.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import VALIDATION_TTL_SECONDS, record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.runtime_team.configuration import assess_runtime_team
from local_control_center.runtime_team.validation import (
    RUNTIME_TEAM_FRESHNESS_SECONDS,
    runtime_validation_state,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

TEAM = {
    "allowedRuntimes": ["llama_cpp"],
    "roleRuntimes": {"product_owner": "llama_cpp", "developer": "llama_cpp"},
    "roleModels": {"product_owner": "gemma-a", "developer": "qwen-b"},
}


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        ProviderAccountStore(handle).upsert_provider_account(
            {
                "providerId": "llama_cpp",
                "displayName": "llama.cpp",
                "providerType": "local",
                "apiFormat": "openai_compatible",
                "providerFamily": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        yield handle


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def _state(connection, model=None):
    return runtime_validation_state(
        connection, "llama_cpp", max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS, model=model
    )


def test_the_validation_state_can_be_read_per_model(connection):
    record_model_execution(connection, "llama_cpp", "gemma-a", True, "test_prompt", started_at=_ago(3))
    record_model_execution(connection, "llama_cpp", "qwen-b", False, "test_prompt", started_at=_ago(1))
    assert _state(connection, "gemma-a").status == "validated"
    assert _state(connection, "qwen-b").status == "failed"
    assert _state(connection).status == "failed"
    assert _state(connection, "never-tried").status == "never"


def test_the_execution_gate_checks_each_sealed_provider_model_pair(connection):
    record_model_execution(connection, "llama_cpp", "gemma-a", True, "test_prompt", started_at=_ago(3))
    record_model_execution(connection, "llama_cpp", "qwen-b", True, "test_prompt", started_at=_ago(2))
    assert assess_runtime_team(
        connection, TEAM, max_age_seconds=VALIDATION_TTL_SECONDS, only_assigned=True
    ).ready
    record_model_execution(connection, "llama_cpp", "qwen-b", False, "tool_broker", started_at=_ago(1))
    readiness = assess_runtime_team(
        connection, TEAM, max_age_seconds=VALIDATION_TTL_SECONDS, only_assigned=True
    )
    assert readiness.stale_runtimes == (
        {
            "providerId": "llama_cpp",
            "model": "qwen-b",
            "status": "failed",
            "reason": "runtime_validation_failed",
        },
    )
    assert readiness.details()["runtimeIds"] == ["llama_cpp"]
    assert "llama_cpp/qwen-b (runtime_validation_failed)" in readiness.reason()


def test_a_team_sealed_without_role_models_keeps_the_runtime_level_gate(connection):
    record_model_execution(connection, "llama_cpp", "gemma-a", True, "test_prompt", started_at=_ago(2))
    record_model_execution(connection, "llama_cpp", "qwen-b", False, "test_prompt", started_at=_ago(1))
    legacy = {key: value for key, value in TEAM.items() if key != "roleModels"}
    readiness = assess_runtime_team(
        connection, legacy, max_age_seconds=VALIDATION_TTL_SECONDS, only_assigned=True
    )
    assert [(item["providerId"], item["status"]) for item in readiness.stale_runtimes] == [
        ("llama_cpp", "failed")
    ]
    assert "model" not in readiness.stale_runtimes[0]
