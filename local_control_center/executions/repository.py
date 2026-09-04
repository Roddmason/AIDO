"""Persiste la cola operacional sobre los jobs existentes y sus eventos incrementales.

Las transacciones sólo agrupan estado y auditoría; no ejecutan callbacks, red ni procesos.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.jobs_approvals.repository import JobsRepository, StaleWorkerFenceError
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

from .models import EXECUTION_JOB_KIND, TERMINAL_STATUSES


class ExecutionRepository:
    """Mantiene identidad compartida ejecución/job y transiciones terminales idempotentes."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def enqueue(
        self,
        *,
        operation: str,
        workload_class: str,
        arguments: dict[str, Any],
        project_id: str | None,
        cwd: str,
        result_status_code: int,
    ) -> dict[str, Any]:
        """Inserta operación y job atómicamente; el payload de auditoría no contiene el prompt."""
        with immediate_transaction(self.connection):
            job = JobsRepository(self.connection).create_job(
                project_id=project_id or "local-operations",
                kind=EXECUTION_JOB_KIND,
                payload={"operation": operation, "workloadClass": workload_class},
            )["job"]
            self.connection.execute(
                """INSERT INTO operational_executions
                (id, job_id, project_id, operation, workload_class, arguments_json, cwd,
                 status, created_at, result_status_code) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    job["id"],
                    job["id"],
                    project_id,
                    operation,
                    workload_class,
                    json_dumps(arguments),
                    cwd,
                    utc_now(),
                    result_status_code,
                ),
            )
            self.event(job["id"], "execution.queued", {"operation": operation})
        return self.get(job["id"])

    def get(self, execution_id: str) -> dict[str, Any]:
        """Lee el estado efectivo, incluyendo backpressure proveniente del scheduler."""
        row = self.connection.execute(
            """SELECT e.*, j.status AS job_status, j.payload AS job_payload
            FROM operational_executions e JOIN jobs j ON j.id=e.job_id WHERE e.id=?""",
            (execution_id,),
        ).fetchone()
        if not row:
            raise KeyError("Execution not found.")
        status = row["status"]
        if status == "queued" and row["job_status"] == "resource_wait":
            status = "resource_wait"
        return {
            "executionId": row["id"],
            "jobId": row["job_id"],
            "projectId": row["project_id"],
            "operation": row["operation"],
            "workloadClass": row["workload_class"],
            "status": status,
            "createdAt": row["created_at"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "cancelRequestedAt": row["cancel_requested_at"],
            "reason": row["reason"],
            "result": json_loads(row["result_json"]) if row["result_json"] is not None else None,
            "resultStatusCode": row["result_status_code"],
        }

    def attach_claimed_job(
        self, job: dict, *, workload_class: str, cwd: str, owner_id: str, fencing_token: int
    ) -> dict:
        """Añade identidad operacional a jobs legacy reclamados, sin crear un segundo job ni replay."""
        with immediate_transaction(self.connection):
            self.require_fence(job["id"], owner_id=owner_id, fencing_token=fencing_token)
            self.connection.execute(
                """INSERT OR IGNORE INTO operational_executions
                (id, job_id, project_id, operation, workload_class, arguments_json, cwd, status, created_at, result_status_code)
                VALUES (?, ?, ?, ?, ?, '{}', ?, 'queued', ?, 200)""",
                (
                    job["id"],
                    job["id"],
                    job["projectId"],
                    "legacy_job:" + job["kind"],
                    workload_class,
                    cwd,
                    utc_now(),
                ),
            )
        return self.get(job["id"])

    def require_fence(self, execution_id: str, *, owner_id: str, fencing_token: int) -> None:
        """Comprueba liderazgo y lease del job; una recuperación invalida al runner antiguo."""
        now = utc_now()
        valid = self.connection.execute(
            """SELECT 1 FROM worker_leader_leases l JOIN jobs j
            ON j.id=? WHERE l.instance_id='local' AND l.owner_id=? AND l.fencing_token=?
            AND l.expires_at>? AND j.lease_owner=? AND j.lease_expires_at>?
            AND j.status IN ('running', 'cancelled')""",
            (execution_id, owner_id, fencing_token, now, owner_id, now),
        ).fetchone()
        if not valid:
            raise StaleWorkerFenceError("El runner no posee la lease y el fencing actuales.")

    def start(self, execution_id: str, *, owner_id: str, fencing_token: int) -> bool:
        """Marca running una vez, después de verificar lease y cancelación dentro de la transacción."""
        with immediate_transaction(self.connection):
            self.require_fence(execution_id, owner_id=owner_id, fencing_token=fencing_token)
            current = self.get(execution_id)
            if current["status"] in TERMINAL_STATUSES:
                return False
            reason = ManagedProcessRepository(self.connection).cancellation_reason(execution_id)
            if reason:
                self.connection.execute(
                    "UPDATE operational_executions SET status='cancelled', finished_at=?, reason=? WHERE id=?",
                    (utc_now(), reason, execution_id),
                )
                self.event(execution_id, "execution.cancelled", {"reason": reason})
                return False
            if current["status"] == "running":
                raise RuntimeError("La ejecución ya tiene un runner activo.")
            self.connection.execute(
                """UPDATE operational_executions SET status='running', started_at=?,
                owner_id=?, fencing_token=? WHERE id=?""",
                (utc_now(), owner_id, fencing_token, execution_id),
            )
            self.event(execution_id, "execution.running", {"ownerId": owner_id})
        return True

    def finish(
        self,
        execution_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        status: str,
        result: Any = None,
        reason: str = "",
        result_status_code: int | None = None,
    ) -> dict[str, Any]:
        """Confirma resultado bajo fencing; una cancelación comprometida tiene precedencia."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("Terminal execution status required.")
        with immediate_transaction(self.connection):
            self.require_fence(execution_id, owner_id=owner_id, fencing_token=fencing_token)
            current = self.get(execution_id)
            if current["status"] in TERMINAL_STATUSES:
                return current
            cancel = ManagedProcessRepository(self.connection).cancellation_reason(execution_id)
            if cancel:
                status, reason = "cancelled", cancel
            self.connection.execute(
                """UPDATE operational_executions SET status=?, finished_at=?,
                result_json=?, reason=?, result_status_code=COALESCE(?, result_status_code) WHERE id=?""",
                (
                    status,
                    utc_now(),
                    json_dumps(redact_secrets(result)),
                    str(redact_secrets(reason)),
                    result_status_code,
                    execution_id,
                ),
            )
            self.event(execution_id, f"execution.{status}", {"reason": reason})
        return self.get(execution_id)

    def request_cancel(self, execution_id: str, *, reason: str) -> dict[str, Any]:
        """Cancela pendientes o solicita el cierre físico de una operación activa."""
        with immediate_transaction(self.connection):
            current = self.get(execution_id)
            if current["status"] in TERMINAL_STATUSES:
                return current
            if current["cancelRequestedAt"]:
                return current
            status = "cancelled" if current["status"] in {"queued", "resource_wait"} else "cancel_requested"
            self.connection.execute(
                """UPDATE operational_executions SET status=?, cancel_requested_at=?,
                reason=?, finished_at=? WHERE id=?""",
                (
                    status,
                    utc_now(),
                    str(redact_secrets(reason)),
                    utc_now() if status == "cancelled" else None,
                    execution_id,
                ),
            )
            JobsRepository(self.connection).cancel_job(execution_id, reason=reason)
            self.event(execution_id, f"execution.{status}", {"reason": reason})
        return self.get(execution_id)

    def event(self, execution_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Anexa un evento sin argumentos productivos ni credenciales."""
        EventBus(self.connection).record_event(
            job_id=execution_id, event_type=event_type, payload={"executionId": execution_id, **payload}
        )

    def events(self, execution_id: str, *, after_seq: int, limit: int) -> dict[str, Any]:
        """Pagina la bitácora compartida con jobs, procesos y worker."""
        self.get(execution_id)
        rows = self.connection.execute(
            """SELECT rowid AS seq, type, payload, created_at FROM events
            WHERE job_id=? AND rowid>? ORDER BY rowid LIMIT ?""",
            (execution_id, after_seq, limit),
        ).fetchall()
        events = [
            {
                "seq": row["seq"],
                "type": row["type"],
                "payload": json_loads(row["payload"], {}),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
        return {
            "executionId": execution_id,
            "events": events,
            "nextSeq": rows[-1]["seq"] if rows else after_seq,
        }
