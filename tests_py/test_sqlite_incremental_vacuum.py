"""Tests: la BD nueva nace en auto_vacuum incremental, el vacuum incremental solo corre en ese modo,
y el comando de mantenimiento se niega a compactar con el worker vivo.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.maintenance import WorkerAliveError, compact_db
from local_control_center.shared.db import incremental_vacuum_if_enabled, open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workers.leadership import WorkerLeadershipRepository


def test_a_brand_new_database_is_created_in_incremental_auto_vacuum_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection:
        with connection:
            initialize_platform_schema(connection)
        mode = connection.execute("PRAGMA auto_vacuum").fetchone()[0]
    assert mode == 2


def test_an_existing_full_database_keeps_its_current_auto_vacuum_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection:
        with connection:
            initialize_platform_schema(connection)
        # Re-aplicar sobre una BD ya completa no debe tocar el modo (solo cambia con VACUUM).
        with connection:
            initialize_platform_schema(connection)
        mode = connection.execute("PRAGMA auto_vacuum").fetchone()[0]
    assert mode == 2


def test_incremental_vacuum_runs_only_in_incremental_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection:
        with connection:
            initialize_platform_schema(connection)
        assert incremental_vacuum_if_enabled(connection, pages=64) is True


def test_incremental_vacuum_is_a_noop_outside_incremental_mode() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE t (id INTEGER)")
        assert int(connection.execute("PRAGMA auto_vacuum").fetchone()[0]) == 0
        assert incremental_vacuum_if_enabled(connection, pages=64) is False
    finally:
        connection.close()


def test_compact_db_refuses_when_the_worker_heartbeat_is_recent(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection:
        with connection:
            initialize_platform_schema(connection)
        WorkerLeadershipRepository(connection).acquire(owner_id="local-worker")

    with pytest.raises(WorkerAliveError):
        compact_db(db_path)


def test_compact_db_compacts_when_no_worker_heartbeat_is_present(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)

    result = compact_db(db_path)

    assert result["sizeBeforeBytes"] > 0
    assert result["sizeAfterBytes"] > 0
    with closing(open_sqlite_connection(db_path)) as connection:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
