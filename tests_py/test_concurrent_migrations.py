"""Migraciones concurrentes: la API y el worker arrancan a la vez y migran la misma base.

Reporte del operador: tras actualizar, ``pnpm run start`` falló en la fase 52 con
"sqlite3.OperationalError: database is locked". El supervisor lanza ambos procesos juntos y cada
fase lee y luego escribe dentro de una transacción diferida que SQLite no puede promover si el
otro proceso escribió entre medio.

@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import CURRENT_SCHEMA_VERSION

MIGRATE = (
    "import sys\n"
    "from contextlib import closing\n"
    "from local_control_center.shared.db import open_sqlite_connection\n"
    "from local_control_center.shared.migrations import initialize_platform_schema\n"
    "with closing(open_sqlite_connection(sys.argv[1])) as connection:\n"
    "    initialize_platform_schema(connection)\n"
)


@pytest.mark.parametrize("round_number", range(3))
def test_processes_migrating_the_same_fresh_database_at_once_all_succeed(
    tmp_path: Path, round_number: int
) -> None:
    # Sin el lock esta carrera fallaba con "database is locked" o "duplicate column name".
    database = tmp_path / f"platform-{round_number}.sqlite"
    root = Path(__file__).resolve().parents[1]
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", MIGRATE, str(database)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    results = [process.communicate(timeout=300) for process in processes]
    failures = [stderr for process, (_, stderr) in zip(processes, results, strict=True) if process.returncode]
    assert not failures, failures[0][-2000:]
    with closing(open_sqlite_connection(database)) as connection:
        applied = {int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")}
    assert applied.issuperset(range(1, CURRENT_SCHEMA_VERSION + 1))
