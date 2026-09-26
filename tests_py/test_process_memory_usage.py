"""RSS real por lease: identidad verificada por create_time, nunca el pico histórico.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import closing

import pytest

from local_control_center.process_supervision.memory_usage import live_memory_by_lease
from local_control_center.process_supervision.models import ProcessLaunchSpec, ProcessStats
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now


def _spec(managed_process_id: str, execution_id: str) -> ProcessLaunchSpec:
    return ProcessLaunchSpec(
        managed_process_id=managed_process_id,
        execution_id=execution_id,
        argv=["test"],
        cwd=".",
        workload_class="agent_cli",
        command_fingerprint="test-fingerprint",
        memory_limit_bytes=1,
        process_limit=1,
        cpu_limit_percent=10,
        below_normal_priority=False,
    )


def test_live_lease_with_a_real_child_process_reports_positive_rss(tmp_path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
            initialize_platform_schema(connection)
            ManagedProcessRepository(connection).start(
                _spec("managed-live", "execution-live"),
                root_pid=process.pid,
                resource_lease_id="lease-live",
            )
            usage = live_memory_by_lease(connection)
        assert usage.get("lease-live", 0) > 0
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_finished_process_is_not_counted_even_with_a_lease_id(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        repository = ManagedProcessRepository(connection)
        repository.start(
            _spec("managed-done", "execution-done"), root_pid=os.getpid(), resource_lease_id="lease-done"
        )
        repository.finish("managed-done", stats=ProcessStats(exit_code=0))
        usage = live_memory_by_lease(connection)
    assert "lease-done" not in usage


@pytest.mark.parametrize("scenario", ["missing_pid", "mismatched_create_time", "unrecorded_create_time"])
def test_unverifiable_identity_sums_zero_but_the_lease_still_appears(tmp_path, scenario) -> None:
    """La fila sigue viva (``finished_at IS NULL``): documenta 0, nunca ausencia."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        repository = ManagedProcessRepository(connection)
        root_pid = 999999 if scenario == "missing_pid" else os.getpid()
        repository.start(_spec("managed-x", "execution-x"), root_pid=root_pid, resource_lease_id="lease-x")
        if scenario != "missing_pid":
            # 1.0 no coincide con el proceso; 0 es el default de columnas migradas sin hora registrada.
            create_time = 1.0 if scenario == "mismatched_create_time" else 0
            connection.execute(
                "UPDATE managed_processes SET root_create_time = ? WHERE managed_process_id = 'managed-x'",
                (create_time,),
            )
        usage = live_memory_by_lease(connection)
    assert "lease-x" in usage
    assert usage["lease-x"] == 0


def test_lease_without_any_live_process_row_is_absent(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        usage = live_memory_by_lease(connection)
    assert usage == {}


def test_a_live_container_makes_its_lease_a_candidate_with_unmeasured_zero_usage(tmp_path) -> None:
    """Docker no expone RSS por este camino (ver ADR-005): la lease aparece con 0, no ausente."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            INSERT INTO managed_containers
                (name, execution_id, executable, owner_pid, owner_create_time, resource_lease_id,
                 owns_lease, created_at, released_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            ("aido-container-test", "execution-container", "docker", 1, 1.0, "lease-container", 1, utc_now()),
        )
        usage = live_memory_by_lease(connection)
    assert "lease-container" in usage
    assert usage["lease-container"] == 0


def test_a_released_container_does_not_make_its_lease_a_candidate(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        connection.execute(
            """
            INSERT INTO managed_containers
                (name, execution_id, executable, owner_pid, owner_create_time, resource_lease_id,
                 owns_lease, created_at, released_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aido-container-released",
                "execution-released",
                "docker",
                1,
                1.0,
                "lease-released",
                1,
                utc_now(),
                utc_now(),
            ),
        )
        usage = live_memory_by_lease(connection)
    assert "lease-released" not in usage
