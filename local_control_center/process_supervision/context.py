"""Propagación explícita de identidad, conexión y reserva de la ejecución productiva.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcessExecutionContext:
    """Identidad heredada por cada etapa de un job, sin compartir conexiones entre hilos."""

    db_path: Path
    execution_id: str | None = None
    project_id: str | None = None
    resource_lease_id: str | None = None
    connection: sqlite3.Connection | None = None
    worker_id: str | None = None
    fencing_token: int | None = None
    in_job_runner: bool = False
    request_id: str | None = None
    attempt_id: str | None = None
    diagnostics_expires_at: float = 0
    aggregate_managed_process_id: str | None = None
    session_role: str | None = None


CURRENT_EXECUTION: ContextVar[ProcessExecutionContext | None] = ContextVar("aido_execution", default=None)


@contextmanager
def execution_scope(context: ProcessExecutionContext) -> Iterator[ProcessExecutionContext]:
    """Vincula identidad a la operación actual y restaura el contexto al salir."""
    token = CURRENT_EXECUTION.set(context)
    try:
        yield context
    finally:
        CURRENT_EXECUTION.reset(token)


def connection_execution_scope(method):
    """Vincula el broker a su base real; conserva la reserva del job cuando está presente."""

    @wraps(method)
    def wrapped(self, *args: Any, **kwargs: Any):
        connection = self.connection
        if connection is None:
            return method(self, *args, **kwargs)
        existing = CURRENT_EXECUTION.get()
        row = connection.execute("PRAGMA database_list").fetchone()
        if existing is not None or not row or not row[2]:
            return method(self, *args, **kwargs)
        with execution_scope(
            ProcessExecutionContext(
                db_path=Path(row[2]),
                execution_id=kwargs.get("job_id") or kwargs.get("agent_run_id"),
                project_id=kwargs.get("project_id"),
                connection=connection,
            )
        ):
            return method(self, *args, **kwargs)

    return wrapped


def assert_external_boundary() -> None:
    """Rechaza esperas externas bajo una transacción de la operación actual."""
    context = CURRENT_EXECUTION.get()
    if context is not None and context.connection is not None and context.connection.in_transaction:
        raise RuntimeError("No se permite ejecución externa dentro de una transacción SQLite.")
