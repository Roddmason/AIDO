"""Tests: el health-check de proveedor no persiste el catalogo completo de modelos, y su retencion
conserva siempre la ultima fila por proveedor.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.provider_health_retention import (
    PROVIDER_HEALTH_CHECK_RETENTION_SECONDS,
    prune_provider_health_checks,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_loads


def _iso(days_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
        yield handle


def test_record_health_check_does_not_persist_the_full_model_list(connection) -> None:
    store = ProviderAccountStore(connection)
    models = [f"model-{index}" for index in range(98)]
    store.record_health_check(
        provider_id="ollama", status="available", payload={"healthStatus": "healthy", "models": models}
    )
    row = connection.execute(
        "SELECT payload FROM provider_health_checks WHERE provider_id = 'ollama'"
    ).fetchone()
    stored = json_loads(row["payload"], {})
    assert stored["modelCount"] == 98
    assert stored["models"] == models[:20]


def test_record_health_check_leaves_small_payloads_untouched(connection) -> None:
    store = ProviderAccountStore(connection)
    result = store.record_health_check(
        provider_id="ollama", status="healthy", payload={"healthStatus": "healthy", "latencyMs": 42}
    )
    assert result["payload"] == {"healthStatus": "healthy", "latencyMs": 42}
    assert "modelCount" not in result["payload"]


def _insert_health_check(connection, *, provider_id: str, days_ago: float, identifier: str) -> None:
    connection.execute(
        "INSERT INTO provider_health_checks (id, provider_id, status, payload, created_at) "
        "VALUES (?, ?, 'healthy', '{}', ?)",
        (identifier, provider_id, _iso(days_ago)),
    )


def test_pruning_keeps_the_latest_row_per_provider_even_if_old(connection) -> None:
    with connection:
        _insert_health_check(connection, provider_id="ollama", days_ago=30, identifier="old")
        _insert_health_check(connection, provider_id="ollama", days_ago=20, identifier="latest-but-old")

    removed = prune_provider_health_checks(
        connection, retention_seconds=PROVIDER_HEALTH_CHECK_RETENTION_SECONDS
    )

    assert removed == 1
    remaining = [row["id"] for row in connection.execute("SELECT id FROM provider_health_checks")]
    assert remaining == ["latest-but-old"]


def test_pruning_removes_only_rows_older_than_the_retention_window(connection) -> None:
    with connection:
        _insert_health_check(connection, provider_id="ollama", days_ago=10, identifier="stale")
        _insert_health_check(connection, provider_id="ollama", days_ago=0.1, identifier="ollama-latest")
        _insert_health_check(connection, provider_id="openai", days_ago=1, identifier="recent")

    removed = prune_provider_health_checks(
        connection, retention_seconds=PROVIDER_HEALTH_CHECK_RETENTION_SECONDS
    )

    assert removed == 1
    remaining = {row["id"] for row in connection.execute("SELECT id FROM provider_health_checks")}
    assert remaining == {"ollama-latest", "recent"}


def test_pruning_drains_in_bounded_batches(connection) -> None:
    with connection:
        for index in range(12):
            _insert_health_check(connection, provider_id="ollama", days_ago=30, identifier=f"old-{index}")
        _insert_health_check(connection, provider_id="ollama", days_ago=0.1, identifier="latest")

    assert prune_provider_health_checks(connection, batch_size=5, max_batches=1) == 5
    assert prune_provider_health_checks(connection, batch_size=5) == 7
    remaining = {row["id"] for row in connection.execute("SELECT id FROM provider_health_checks")}
    assert remaining == {"latest"}
