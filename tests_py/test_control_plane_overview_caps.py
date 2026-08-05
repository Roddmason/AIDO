"""Caps deterministas del overview: las colecciones históricas conservan solo el tail reciente.

Cubre la regresión de payload sin techo: threads/jobRuns/workflowEvents crecen sin límite
natural y el snapshot se sirve en el poll de 5 s del dashboard. Con ``limit`` los repos
devuelven exactamente N filas (las más recientes, orden estable por rowid) y con
``limit=None`` el comportamiento previo queda intacto.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from local_control_center.control_plane import overview as overview_module
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workflows.repository import WorkflowsRepository


def _seed_threads(connection: sqlite3.Connection, count: int) -> None:
    for position in range(count):
        connection.execute(
            """
            INSERT INTO project_threads
                (id, project_id, owner_type, owner_id, title, status, summary, metadata,
                 created_at, updated_at)
            VALUES (?, 'project-caps', 'workspace', 'workspace-1', ?, 'active', '', '{}', ?, ?)
            """,
            (
                f"thread-{position:03d}",
                f"Thread {position:03d}",
                f"2026-01-01T00:00:{position:02d}Z",
                f"2026-01-01T00:00:{position:02d}Z",
            ),
        )


def _seed_job_runs(connection: sqlite3.Connection, count: int) -> None:
    for position in range(count):
        connection.execute(
            """
            INSERT INTO job_runs (id, job_id, provider_id, status, started_at, completed_at, summary, metadata)
            VALUES (?, 'job-caps', NULL, 'succeeded', ?, NULL, '', '{}')
            """,
            (f"job-run-{position:03d}", f"2026-01-01T00:00:{position:02d}Z"),
        )


def _seed_workflow_events(connection: sqlite3.Connection, count: int) -> None:
    for position in range(count):
        connection.execute(
            """
            INSERT INTO workflow_events
                (id, workflow_id, workflow_run_id, step_id, project_id, type, payload, severity, created_at)
            VALUES (?, 'workflow-caps', NULL, NULL, NULL, 'test.event', '{}', 'info', ?)
            """,
            (f"workflow-event-{position:03d}", f"2026-01-01T00:00:{position:02d}Z"),
        )


@pytest.fixture()
def seeded_connection(tmp_path: Path):
    """Base con 8 filas por colección histórica representativa."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        _seed_threads(connection, 8)
        _seed_job_runs(connection, 8)
        _seed_workflow_events(connection, 8)
        yield connection


def test_repo_limits_keep_most_recent_rows_with_stable_order(seeded_connection) -> None:
    threads = ThreadsRepository(seeded_connection).list_threads(limit=5)
    assert [item["id"] for item in threads] == [f"thread-{position:03d}" for position in range(7, 2, -1)]

    job_runs = JobsRepository(seeded_connection).list_job_runs(limit=5)
    assert [item["id"] for item in job_runs] == [f"job-run-{position:03d}" for position in range(3, 8)]

    events = WorkflowsRepository(seeded_connection).list_workflow_events(limit=5)
    assert [item["id"] for item in events] == [
        f"workflow-event-{position:03d}" for position in range(7, 2, -1)
    ]


def test_repo_limit_none_returns_everything(seeded_connection) -> None:
    assert len(ThreadsRepository(seeded_connection).list_threads()) == 8
    assert len(JobsRepository(seeded_connection).list_job_runs()) == 8
    assert len(WorkflowsRepository(seeded_connection).list_workflow_events()) == 8


def test_overview_applies_historic_collection_caps(seeded_connection, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(overview_module, "OVERVIEW_THREAD_LIMIT", 5)
    monkeypatch.setattr(overview_module, "OVERVIEW_JOB_RUN_LIMIT", 5)
    monkeypatch.setattr(overview_module, "OVERVIEW_WORKFLOW_EVENT_LIMIT", 5)

    snapshot = overview_module.build_overview_from_connection(connection=seeded_connection, cwd=tmp_path)

    assert len(snapshot["threads"]) == 5
    assert snapshot["threads"][0]["id"] == "thread-007"
    assert len(snapshot["jobRuns"]) == 5
    assert snapshot["jobRuns"][-1]["id"] == "job-run-007"
    assert len(snapshot["workflowEvents"]) == 5
    assert snapshot["workflowEvents"][0]["id"] == "workflow-event-007"
