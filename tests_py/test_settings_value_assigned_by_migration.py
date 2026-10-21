"""`settings_value` conserva una sola columna de procedencia (`assigned_by`) tras reinicializar.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import (
    init_phase72_schema,
    initialize_platform_schema,
)


def _columns(connection) -> list[str]:
    return [row["name"] for row in connection.execute("PRAGMA table_info(settings_value)")]


def test_phase72_does_not_reintroduce_origin_after_the_rename(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "aido.sqlite")) as connection:
        initialize_platform_schema(connection)
        init_phase72_schema(connection)

        assert "origin" not in _columns(connection)
        assert "assigned_by" in _columns(connection)


def test_stale_origin_column_is_dropped_keeping_assigned_by(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "aido.sqlite")) as connection:
        initialize_platform_schema(connection)
        connection.execute("ALTER TABLE settings_value ADD COLUMN origin TEXT NOT NULL DEFAULT 'operator'")
        connection.execute("DELETE FROM schema_migrations WHERE version = 86")
        connection.commit()

        initialize_platform_schema(connection)

        assert "origin" not in _columns(connection)
        assert "assigned_by" in _columns(connection)
