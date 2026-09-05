"""Apertura de la conexión SQLite y helper de transacciones atómicas.

Configura la conexión con los PRAGMA de durabilidad/concurrencia (WAL, foreign
keys, busy_timeout) y autocommit, e implementa el límite transaccional explícito
del backend: ``BEGIN IMMEDIATE`` con COMMIT al salir o ROLLBACK ante cualquier excepción.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def open_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Abre la SQLite creando su directorio y fija PRAGMA de WAL, foreign keys y autocommit."""
    require_safe_sqlite_runtime()
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


def require_safe_sqlite_runtime() -> None:
    """Rechaza runtimes afectados por WAL-reset y la versión 3.52 retirada por SQLite.

    Fuente: https://www.sqlite.org/wal.html#walresetbug y https://www.sqlite.org/news.html.
    La verificación ocurre antes de crear la base: actualizar Python/SQLite, no desactivar WAL.
    """
    version = sqlite3.sqlite_version_info
    supported = (
        (version >= (3, 51, 3) and version[:2] != (3, 52))
        or (version[:2] == (3, 50) and version >= (3, 50, 7))
        or (version[:2] == (3, 44) and version >= (3, 44, 6))
    )
    if not supported:
        raise RuntimeError(
            "SQLite runtime requires the WAL-reset fix: use 3.51.3+ (excluding withdrawn 3.52), "
            "or patched 3.50.7/3.44.6. Upgrade the project Python runtime before starting AIDO."
        )


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


def sqlite_database_diagnostics(
    connection: sqlite3.Connection,
    *,
    db_path: str | Path,
) -> dict[str, Any]:
    """Devuelve señales operacionales de SQLite/WAL sin iniciar una transacción de escritura."""
    resolved = Path(db_path)
    journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
    busy_timeout_ms = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
    wal_autocheckpoint_pages = int(connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0])
    page_size_bytes = int(connection.execute("PRAGMA page_size").fetchone()[0])
    wal_path = Path(f"{resolved}-wal")
    shm_path = Path(f"{resolved}-shm")
    return {
        "sqliteVersion": sqlite3.sqlite_version,
        "journalMode": journal_mode,
        "busyTimeoutMs": busy_timeout_ms,
        "walAutoCheckpointPages": wal_autocheckpoint_pages,
        "pageSizeBytes": page_size_bytes,
        "databaseBytes": resolved.stat().st_size if resolved.exists() else 0,
        "walBytes": wal_path.stat().st_size if wal_path.exists() else 0,
        "sharedMemoryBytes": shm_path.stat().st_size if shm_path.exists() else 0,
    }


def passive_wal_checkpoint(
    connection: sqlite3.Connection,
    *,
    db_path: str | Path,
) -> dict[str, Any]:
    """Ejecuta ``wal_checkpoint(PASSIVE)`` y reporta progreso sin bloquear lectores/escritores."""
    started = time.perf_counter()
    busy, log_frames, checkpointed_frames = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    wal_path = Path(f"{Path(db_path)}-wal")
    return {
        "mode": "PASSIVE",
        "busy": int(busy),
        "logFrames": max(0, int(log_frames)),
        "checkpointedFrames": max(0, int(checkpointed_frames)),
        "walBytes": wal_path.stat().st_size if wal_path.exists() else 0,
        "durationMs": max(0, int((time.perf_counter() - started) * 1000)),
    }
