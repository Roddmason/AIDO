"""Apertura de la conexión SQLite y helper de transacciones atómicas.

Configura la conexión con los PRAGMA de durabilidad/concurrencia (WAL, foreign
keys, busy_timeout) y autocommit, e implementa el límite transaccional explícito
del backend: ``BEGIN IMMEDIATE`` con COMMIT al salir o ROLLBACK ante cualquier excepción.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def open_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Abre la SQLite creando su directorio y fija PRAGMA de WAL, foreign keys y autocommit."""
    resolved = Path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        resolved,
        timeout=30,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Abre una transacción atómica (``BEGIN IMMEDIATE``): COMMIT al salir, ROLLBACK si algo falla."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
