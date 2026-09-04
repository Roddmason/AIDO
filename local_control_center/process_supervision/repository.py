"""Persistencia SQLite de procesos administrados sin command lines ni prompts.

Operaciones en autocommit; las transacciones compuestas pertenecen al caller.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sqlite3

import psutil

from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .models import ManagedProcessRecord, ProcessLaunchSpec, ProcessStats


def _record(row: sqlite3.Row) -> ManagedProcessRecord:
    return ManagedProcessRecord(
        managed_process_id=row["managed_process_id"],
        execution_id=row["execution_id"],
        root_pid=int(row["root_pid"]),
        workload_class=row["workload_class"],
        command_fingerprint=row["command_fingerprint"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        exit_code=row["exit_code"],
        timed_out=bool(row["timed_out"]),
        cancelled=bool(row["cancelled"]),
        peak_memory_bytes=int(row["peak_memory_bytes"]),
        cpu_time_seconds=float(row["cpu_time_seconds"]),
        stdout_artifact_id=row["stdout_artifact_id"],
        stderr_artifact_id=row["stderr_artifact_id"],
        termination_reason=row["termination_reason"],
        cancel_requested_at=row["cancel_requested_at"],
        released_at=row["released_at"],
        resource_lease_id=row["resource_lease_id"],
        owner_pid=row["owner_pid"],
        owner_create_time=row["owner_create_time"],
        root_create_time=row["root_create_time"],
    )


class ManagedProcessRepository:
    """Escribe el ciclo de vida terminal e idempotente de cada árbol AIDO."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def start(
        self, spec: ProcessLaunchSpec, *, root_pid: int, resource_lease_id: str | None = None
    ) -> ManagedProcessRecord:
        """Registra el proceso raíz usando sólo el fingerprint seguro del comando."""
        self.connection.execute(
            """
            INSERT INTO managed_processes
                (managed_process_id, execution_id, root_pid, workload_class,
                 command_fingerprint, started_at, resource_lease_id, owner_pid,
                 owner_create_time, root_create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.managed_process_id,
                spec.execution_id,
                root_pid,
                spec.workload_class,
                spec.command_fingerprint,
                utc_now(),
                resource_lease_id,
                os.getpid(),
                process_create_time(os.getpid()),
                process_create_time(root_pid),
            ),
        )
        record = self.get(spec.managed_process_id)
        assert record is not None
        return record

    def get(self, managed_process_id: str) -> ManagedProcessRecord | None:
        """Lee una ejecución por su identificador opaco."""
        row = self.connection.execute(
            "SELECT * FROM managed_processes WHERE managed_process_id = ?",
            (managed_process_id,),
        ).fetchone()
        return _record(row) if row else None

    def active(self) -> list[ManagedProcessRecord]:
        """Lista sólo procesos que aún no registran resultado terminal."""
        rows = self.connection.execute(
            "SELECT * FROM managed_processes WHERE finished_at IS NULL ORDER BY started_at"
        ).fetchall()
        return [_record(row) for row in rows]

    def request_cancel(self, managed_process_id: str, *, reason: str) -> ManagedProcessRecord | None:
        """Registra la solicitud de cancelación antes de tocar el proceso nativo."""
        now = utc_now()
        self.connection.execute(
            """
            UPDATE managed_processes
            SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                termination_reason = CASE WHEN termination_reason = '' THEN ? ELSE termination_reason END
            WHERE managed_process_id = ? AND finished_at IS NULL
            """,
            (now, str(redact_secrets(reason)), managed_process_id),
        )
        record = self.get(managed_process_id)
        if record and not record.finished_at:
            self.request_execution_cancel(record.execution_id, reason=reason)
        return record

    def request_execution_cancel(self, execution_id: str, *, reason: str) -> None:
        """Cancela la ejecución lógica, incluidas etapas que todavía no tienen proceso."""
        clean_reason = str(redact_secrets(reason)).strip()
        if not clean_reason:
            raise ValueError("Cancellation reason is required.")
        cursor = self.connection.execute(
            """
            INSERT INTO process_execution_controls (execution_id, cancel_requested_at, reason)
            VALUES (?, ?, ?) ON CONFLICT(execution_id) DO NOTHING
        """,
            (execution_id, utc_now(), clean_reason),
        )
        self.connection.execute(
            """
            UPDATE managed_processes SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                termination_reason = CASE WHEN termination_reason = '' THEN ? ELSE termination_reason END
            WHERE execution_id = ? AND finished_at IS NULL
        """,
            (utc_now(), clean_reason, execution_id),
        )
        if cursor.rowcount:
            bus = EventBus(self.connection)
            bus.record_event(
                event_type="execution.cancel_requested",
                payload={"executionId": execution_id, "reason": clean_reason},
            )
            bus.record_audit(
                action="execution.cancel",
                target=execution_id,
                actor="operator",
                payload={"reason": clean_reason},
            )

    def cancellation_reason(self, execution_id: str) -> str | None:
        """Lee controles compartidos entre API y worker sin depender de memoria local."""
        request = self.connection.execute(
            "SELECT reason FROM process_execution_controls WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if request:
            return str(request[0])
        control = self.connection.execute(
            "SELECT desired_state, reason FROM worker_control_state WHERE instance_id = 'local'"
        ).fetchone()
        if control and control[0] == "emergency_stopped":
            return str(control[1] or "emergency_stop")
        violation = self.connection.execute(
            """
            SELECT reason FROM resource_violations WHERE execution_id = ?
                AND resolved_at IS NULL AND action = 'cancel_non_essential_workload' LIMIT 1
        """,
            (execution_id,),
        ).fetchone()
        return str(violation[0]) if violation else None

    def finish(
        self,
        managed_process_id: str,
        *,
        stats: ProcessStats,
        stdout_artifact_id: str | None = None,
        stderr_artifact_id: str | None = None,
    ) -> ManagedProcessRecord:
        """Guarda la transición terminal; la reserva del job pertenece al worker."""
        previous = self.get(managed_process_id)
        now = utc_now()
        self.connection.execute(
            """
            UPDATE managed_processes
            SET finished_at = COALESCE(finished_at, ?),
                exit_code = COALESCE(exit_code, ?),
                timed_out = MAX(timed_out, ?),
                cancelled = MAX(cancelled, ?),
                peak_memory_bytes = MAX(peak_memory_bytes, ?),
                cpu_time_seconds = MAX(cpu_time_seconds, ?),
                stdout_artifact_id = COALESCE(stdout_artifact_id, ?),
                stderr_artifact_id = COALESCE(stderr_artifact_id, ?),
                termination_reason = CASE
                    WHEN termination_reason = '' THEN ? ELSE termination_reason END,
                released_at = COALESCE(released_at, ?)
            WHERE managed_process_id = ?
            """,
            (
                now,
                stats.exit_code,
                int(stats.timed_out),
                int(stats.cancelled),
                stats.peak_memory_bytes,
                stats.cpu_time_seconds,
                stdout_artifact_id,
                stderr_artifact_id,
                stats.termination_reason,
                now,
                managed_process_id,
            ),
        )
        record = self.get(managed_process_id)
        if record is None:
            raise KeyError(f"Unknown managed process: {managed_process_id}")
        if previous and not previous.finished_at:
            payload = {
                "executionId": record.execution_id,
                "managedProcessId": managed_process_id,
                "cancelled": record.cancelled,
                "timedOut": record.timed_out,
                "exitCode": record.exit_code,
                "reason": record.termination_reason,
            }
            bus = EventBus(self.connection)
            bus.record_event(event_type="process.finished", payload=payload)
            bus.record_audit(action="process.finished", target=managed_process_id, payload=payload)
        return record


def process_create_time(pid: int) -> float:
    """Obtiene identidad resistente a reutilización de PID; cero representa ausencia."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return 0
