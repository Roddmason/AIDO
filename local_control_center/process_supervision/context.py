"""Propagación explícita de identidad, conexión y reserva de la ejecución productiva.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
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
    execution_deadline_monotonic: float | None = None


CURRENT_EXECUTION: ContextVar[ProcessExecutionContext | None] = ContextVar("aido_execution", default=None)


class ExecutionDeadlineExceeded(TimeoutError):
    """The parent execution budget expired; provider failover cannot renew it."""


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


def runner_execution_deadline(connection, *, execution_id: str, runner_pid: int) -> float:
    """Anchor one 900-second envelope to the registered native runner, including launcher siblings."""
    import psutil

    execution = connection.execute(
        "SELECT started_at FROM operational_executions WHERE id=?", (execution_id,)
    ).fetchone()
    if not execution or not execution["started_at"]:
        raise ValueError("The runner has no persisted execution start for its deadline.")

    def timestamp(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

    starts = [timestamp(execution["started_at"])]
    processes = connection.execute(
        "SELECT root_pid,root_create_time,started_at,finished_at FROM managed_processes WHERE execution_id=?",
        (execution_id,),
    ).fetchall()
    if processes:
        attempt = connection.execute(
            "SELECT started_at,status FROM job_runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
        try:
            runner = psutil.Process(runner_pid)
            identities = {process.pid: process.create_time() for process in [runner, *runner.parents()]}
        except psutil.Error as error:
            raise ValueError("The registered runner identity could not be verified.") from error
        matching = [
            timestamp(process["started_at"])
            for process in processes
            if attempt
            and attempt["status"] == "running"
            and process["finished_at"] is None
            and timestamp(process["started_at"]) >= timestamp(attempt["started_at"])
            and process["root_pid"] in identities
            and process["root_create_time"] > 0
            and abs(identities[process["root_pid"]] - process["root_create_time"]) < 0.01
        ]
        if not matching:
            raise ValueError("No registered native runner matches the current attempt and OS identity.")
        starts.extend(matching)
    return time.monotonic() + (min(starts) + 900 - time.time())


def remaining_execution_timeout(requested_seconds: int, *, cleanup_seconds: int = 15) -> int:
    """Share the original envelope across calls; reserve time for evidence and durable closure."""
    context = CURRENT_EXECUTION.get()
    if context is None or context.execution_deadline_monotonic is None:
        return requested_seconds
    remaining = int(context.execution_deadline_monotonic - time.monotonic() - cleanup_seconds)
    if remaining <= 0:
        raise ExecutionDeadlineExceeded(
            "execution_deadline_exhausted: the execution deadline has no remaining runtime budget; partial changes are preserved."
        )
    return min(requested_seconds, remaining)
