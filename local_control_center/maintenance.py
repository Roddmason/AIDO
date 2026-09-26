"""Comando de mantenimiento de la BD del control center: conversion unica a auto_vacuum incremental.

Uso: ``uv run python -m local_control_center.maintenance compact-db --db <ruta>``. Se niega si el
worker de AIDO parece vivo (latido de liderazgo reciente en `worker_leader_leases.heartbeat_at`,
gestionado por `workers.leadership.WorkerLeadershipRepository`; no hay heartbeat de worker en
`jobs_approvals`) para no competir por el candado de escritura con un `VACUUM` que reescribe todo
el archivo.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from local_control_center.shared.db import open_sqlite_connection

LEADER_HEARTBEAT_ALIVE_SECONDS = 60


class WorkerAliveError(RuntimeError):
    """El worker de AIDO parece vivo; compactar ahora competiria por el candado de escritura."""


def _seconds_since(timestamp: str) -> float:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed).total_seconds()


def _refuse_if_worker_alive(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT heartbeat_at FROM worker_leader_leases WHERE instance_id = 'local'"
    ).fetchone()
    if row is None or row["heartbeat_at"] is None:
        return
    age_seconds = _seconds_since(str(row["heartbeat_at"]))
    if age_seconds < LEADER_HEARTBEAT_ALIVE_SECONDS:
        raise WorkerAliveError(
            f"El worker de AIDO parece vivo (latido de liderazgo hace {age_seconds:.1f}s, "
            f"menor a {LEADER_HEARTBEAT_ALIVE_SECONDS}s). Deten AIDO antes de compactar la BD."
        )


def compact_db(db_path: str | Path) -> dict[str, int]:
    """Convierte la BD a `auto_vacuum=INCREMENTAL` con un `VACUUM` unico; se niega con worker vivo.

    Returns:
        Tamano del archivo en bytes antes y despues (``sizeBeforeBytes``/``sizeAfterBytes``).
    """
    resolved = Path(db_path)
    size_before = resolved.stat().st_size
    with closing(open_sqlite_connection(resolved)) as connection:
        _refuse_if_worker_alive(connection)
        connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        connection.execute("VACUUM")
    size_after = resolved.stat().st_size
    return {"sizeBeforeBytes": size_before, "sizeAfterBytes": size_after}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: despacha `compact-db` y devuelve el codigo de salida del proceso."""
    parser = argparse.ArgumentParser(prog="local_control_center.maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    compact_parser = subparsers.add_parser(
        "compact-db", help="Convierte la BD a auto_vacuum incremental y compacta con VACUUM."
    )
    compact_parser.add_argument("--db", required=True, help="Ruta al archivo SQLite de la plataforma.")
    args = parser.parse_args(argv)

    if args.command == "compact-db":
        try:
            result = compact_db(args.db)
        except WorkerAliveError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(
            f"Compactado {args.db}: {result['sizeBeforeBytes']} -> {result['sizeAfterBytes']} bytes "
            f"({result['sizeBeforeBytes'] - result['sizeAfterBytes']} bytes liberados)."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
