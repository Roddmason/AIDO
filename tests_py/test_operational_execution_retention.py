"""Tests: la retencion de operational_executions distingue salud (7 dias) del resto (30 dias) y
nunca borra el ultimo git.refresh completado de cada proyecto.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.executions.retention import (
    OPERATIONAL_DEFAULT_RETENTION_SECONDS,
    OPERATIONAL_HEALTH_RETENTION_SECONDS,
    prune_operational_executions,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _iso(days_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
        yield handle


def _insert_execution(
    connection, *, identifier, operation, status, project_id="proj-1", days_ago=60, finished=True
):
    created = _iso(days_ago)
    finished_at = created if finished else None
    connection.execute(
        """INSERT INTO operational_executions
           (id, job_id, project_id, operation, workload_class, arguments_json, cwd, status,
            created_at, started_at, finished_at, cancel_requested_at, reason, result_json,
            result_status_code)
           VALUES (?, ?, ?, ?, 'control_plane', '{}', '/tmp', ?, ?, ?, ?, NULL, '', NULL, 200)""",
        (identifier, identifier, project_id, operation, status, created, created, finished_at),
    )


def test_health_check_terminal_operations_use_the_short_retention_window(connection) -> None:
    with connection:
        _insert_execution(
            connection,
            identifier="old-health",
            operation="models.provider_health_check",
            status="completed",
            days_ago=8,
        )
        _insert_execution(
            connection,
            identifier="recent-health",
            operation="models.health_cli_runtime",
            status="completed",
            days_ago=1,
        )

    removed = prune_operational_executions(
        connection,
        health_retention_seconds=OPERATIONAL_HEALTH_RETENTION_SECONDS,
        default_retention_seconds=OPERATIONAL_DEFAULT_RETENTION_SECONDS,
    )

    assert removed == 1
    remaining = {row["id"] for row in connection.execute("SELECT id FROM operational_executions")}
    assert remaining == {"recent-health"}


def test_other_terminal_operations_use_the_long_retention_window(connection) -> None:
    with connection:
        _insert_execution(
            connection, identifier="old-op", operation="git.status", status="completed", days_ago=31
        )
        _insert_execution(
            connection, identifier="recent-op", operation="git.status", status="completed", days_ago=8
        )

    removed = prune_operational_executions(connection)

    assert removed == 1
    remaining = {row["id"] for row in connection.execute("SELECT id FROM operational_executions")}
    assert remaining == {"recent-op"}


@pytest.mark.parametrize("status", ["queued", "running", "cancel_requested", "resource_wait"])
def test_non_terminal_executions_are_never_pruned(connection, status) -> None:
    with connection:
        _insert_execution(
            connection,
            identifier="active",
            operation="git.status",
            status=status,
            days_ago=400,
            finished=False,
        )

    removed = prune_operational_executions(connection)

    assert removed == 0
    assert connection.execute("SELECT COUNT(*) FROM operational_executions").fetchone()[0] == 1


def test_the_latest_completed_git_refresh_per_project_always_survives(connection) -> None:
    with connection:
        _insert_execution(
            connection,
            identifier="ancient-refresh",
            operation="git.refresh",
            status="completed",
            project_id="proj-1",
            days_ago=400,
        )
        _insert_execution(
            connection,
            identifier="other-project-refresh",
            operation="git.refresh",
            status="completed",
            project_id="proj-2",
            days_ago=400,
        )

    removed = prune_operational_executions(connection)

    assert removed == 0
    remaining = {row["id"] for row in connection.execute("SELECT id FROM operational_executions")}
    assert remaining == {"ancient-refresh", "other-project-refresh"}


def test_an_older_git_refresh_is_pruned_once_a_newer_one_exists_for_the_project(connection) -> None:
    with connection:
        _insert_execution(
            connection,
            identifier="superseded-refresh",
            operation="git.refresh",
            status="completed",
            project_id="proj-1",
            days_ago=400,
        )
        _insert_execution(
            connection,
            identifier="latest-refresh",
            operation="git.refresh",
            status="completed",
            project_id="proj-1",
            days_ago=1,
        )

    removed = prune_operational_executions(connection)

    assert removed == 1
    remaining = {row["id"] for row in connection.execute("SELECT id FROM operational_executions")}
    assert remaining == {"latest-refresh"}


def test_pruning_drains_in_bounded_batches(connection) -> None:
    with connection:
        for index in range(12):
            _insert_execution(
                connection,
                identifier=f"old-{index}",
                operation="git.status",
                status="completed",
                days_ago=60,
            )

    assert prune_operational_executions(connection, batch_size=5, max_batches=1) == 5
    assert prune_operational_executions(connection, batch_size=5) == 7
