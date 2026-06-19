"""Repositorio SQLite del slice de jobs/aprobaciones: ciclo de vida del job, runs y action requests.

Transacciones: la mayoría de los métodos emiten varios `UPDATE/INSERT` (cambio de estado +
evento + auditoría) y delegan el commit en la transacción del `connection` del caller (modo
autocommit del wrapper). La excepción es `claim_next_job`, que abre `BEGIN IMMEDIATE` y hace
COMMIT/ROLLBACK propios para serializar el reclamo de la cola entre workers concurrentes.
Invariante de seguridad: todo command/payload/reason se redacta con `redact_secrets` antes de
persistir o registrar, de modo que ningún secreto llega a `jobs`, `action_requests` ni eventos.
"""

from __future__ import annotations

import shlex
import sqlite3
import uuid
from collections.abc import Iterable
from typing import Any

from local_control_center.security_policy.repository import SecurityPolicyRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import add_millis, utc_now

from .models import SENSITIVE_JOB_KINDS


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de `jobs` al dict camelCase de la API, deserializando el payload JSON."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "workflowRunId": row["workflow_run_id"],
        "workflowStepId": row["workflow_step_id"],
        "kind": row["kind"],
        "status": row["status"],
        "payload": json_loads(row["payload"]),
        "leaseOwner": row["lease_owner"],
        "leaseExpiresAt": row["lease_expires_at"],
        "idempotencyKey": row["idempotency_key"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_job_run(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de `job_runs` al dict camelCase, deserializando su metadata JSON."""
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "providerId": row["provider_id"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "summary": row["summary"],
        "metadata": json_loads(row["metadata"]),
    }


ACTION_REQUEST_TTL_MS = 24 * 60 * 60 * 1000


def _list_payload_value(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _dict_payload_value(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _command_argv(command: str) -> list[str]:
    command = command.strip()
    if not command:
        return []
    try:
        return [str(item) for item in shlex.split(command)]
    except ValueError:
        return [command]


def _payload_requests_execution(payload: dict[str, Any]) -> bool:
    if payload.get("execute") is True:
        return True
    runtime = payload.get("runtime")
    return isinstance(runtime, dict) and runtime.get("execute") is True


def row_to_action_request(row: sqlite3.Row) -> dict[str, Any]:
    """Proyecta una fila de `action_requests` al dict de la API.

    Rehidrata desde el payload JSON los campos derivados (argv, workspace, runtime, refs de
    evidencia/diff) que no tienen columna propia, tolerando que `expires_at` no exista en
    esquemas antiguos.
    """
    payload = json_loads(row["payload"])
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "projectId": row["project_id"],
        "actionType": row["action_type"],
        "status": row["status"],
        "riskLevel": row["risk_level"],
        "command": row["command"],
        "commandArgv": [str(item) for item in _list_payload_value(payload, "commandArgv", "argv")],
        "workspaceId": payload.get("workspaceId"),
        "workspacePath": payload.get("workspacePath"),
        "workspace": _dict_payload_value(payload, "workspace"),
        "runtimeId": payload.get("runtimeId"),
        "runtime": _dict_payload_value(payload, "runtime"),
        "evidenceRefs": [str(item) for item in _list_payload_value(payload, "evidenceRefs")],
        "diffRefs": _list_payload_value(payload, "diffRefs"),
        "payload": payload,
        "reason": row["reason"],
        "requestedAt": row["requested_at"],
        "expiresAt": row["expires_at"] if "expires_at" in row.keys() else None,
        "decidedAt": row["decided_at"],
        "decidedBy": row["decided_by"],
    }


class JobsRepository:
    """Acceso a `jobs`, `job_runs` y `action_requests` sobre el connection del caller.

    Cada método agrupa el cambio de estado con sus eventos y auditoría; salvo `claim_next_job`
    no abre transacciones explícitas, por lo que confía en la transacción/commit del connection.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, tuple(params)).fetchall()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, tuple(params)).fetchone()

    def create_job(
        self,
        *,
        project_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
        idempotency_key: str | None = None,
        workflow_run_id: str | None = None,
        workflow_step_id: str | None = None,
    ) -> dict[str, Any]:
        """Inserta un job y, si el kind es sensible o el payload lo pide, su action request de aprobación.

        Escribe en una sola transacción del caller: la fila `jobs`, los eventos `job.created`
        (+ `job.approval_required`) y, cuando aplica, la action request. El status inicial es
        `approval_required` si requiere gating, si no `queued`.

        Returns:
            Dict con el job creado, los eventos emitidos y las action requests generadas.
        """
        payload = redact_secrets(payload or {})
        timestamp = utc_now()
        job_id = f"job-{uuid.uuid4()}"
        needs_approval = kind in SENSITIVE_JOB_KINDS or payload.get("approvalRequired") is True
        resolved_status = status or ("approval_required" if needs_approval else "queued")
        self.connection.execute(
            """
            INSERT INTO jobs
                (id, project_id, workflow_run_id, workflow_step_id, kind, status, payload, lease_owner, lease_expires_at,
                 idempotency_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                job_id,
                project_id,
                workflow_run_id or payload.get("workflowRunId"),
                workflow_step_id or payload.get("workflowStepId"),
                kind,
                resolved_status,
                json_dumps(payload),
                idempotency_key,
                timestamp,
                timestamp,
            ),
        )
        events = [
            self.record_event(
                project_id=project_id, job_id=job_id, event_type="job.created", payload={"kind": kind}
            )
        ]
        action_requests: list[dict[str, Any]] = []
        if needs_approval:
            action_requests.append(
                self.create_action_request(
                    job_id=job_id,
                    project_id=project_id,
                    action_type=f"job.{kind}",
                    risk_level="high",
                    command=payload.get("command") or kind,
                    payload=payload,
                    reason="Sensitive job requires granular approval before execution.",
                )
            )
            events.append(
                self.record_event(
                    project_id=project_id,
                    job_id=job_id,
                    event_type="job.approval_required",
                    payload={"kind": kind},
                )
            )
        return {
            "job": self.get_job(job_id),
            "events": events,
            "actionRequests": action_requests,
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Devuelve el job por id.

        Raises:
            KeyError: si el job no existe.
        """
        row = self._query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            raise KeyError(f"Job not found: {job_id}")
        return row_to_job(row)

    def list_jobs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista los jobs (todos o de un proyecto) ordenados del más reciente al más antiguo."""
        if project_id:
            rows = self._query(
                "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
            )
        else:
            rows = self._query("SELECT * FROM jobs ORDER BY created_at DESC")
        return [row_to_job(row) for row in rows]

    def list_jobs_for_workflow_runs(self, workflow_run_ids: list[str]) -> list[dict[str, Any]]:
        """Trae los jobs ligados a un conjunto de workflow runs; lista vacía si no se pasan ids."""
        if not workflow_run_ids:
            return []
        placeholders = ",".join("?" for _ in workflow_run_ids)
        rows = self._query(
            f"SELECT * FROM jobs WHERE workflow_run_id IN ({placeholders}) ORDER BY created_at DESC",
            tuple(workflow_run_ids),
        )
        return [row_to_job(row) for row in rows]

    def create_action_request(
        self,
        *,
        job_id: str,
        project_id: str,
        action_type: str,
        risk_level: str,
        command: str = "",
        command_argv: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        reason: str = "",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Crea una action request `pending` con TTL y emite el evento `action.requested`.

        Redacta command/payload/reason antes de persistir y deriva `commandArgv` desde el argv
        explícito, el payload o el parseo shell del comando. El TTL por defecto es de 24 h.

        Raises:
            ValueError: si la razón queda vacía tras redactar/normalizar.
        """
        action_id = f"action-{uuid.uuid4()}"
        clean_command = str(redact_secrets(command or ""))
        clean_payload = redact_secrets(payload or {})
        clean_reason = str(redact_secrets(reason or ""))
        if not clean_reason.strip():
            raise ValueError("Action request reason is required.")
        if command_argv is not None:
            clean_payload["commandArgv"] = [str(item) for item in command_argv]
        elif not isinstance(clean_payload.get("commandArgv"), list):
            payload_argv = clean_payload.get("argv")
            clean_payload["commandArgv"] = (
                [str(item) for item in payload_argv]
                if isinstance(payload_argv, list)
                else []
                if _payload_requests_execution(clean_payload)
                else _command_argv(clean_command)
            )
        expires_at = expires_at or add_millis(ACTION_REQUEST_TTL_MS)
        self.connection.execute(
            """
            INSERT INTO action_requests
                (id, job_id, project_id, action_type, status, risk_level, command, payload,
                 reason, requested_at, expires_at, decided_at, decided_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                action_id,
                job_id,
                project_id,
                action_type,
                "pending",
                risk_level,
                clean_command,
                json_dumps(clean_payload),
                clean_reason,
                utc_now(),
                expires_at,
            ),
        )
        self.record_event(
            project_id=project_id,
            job_id=job_id,
            event_type="action.requested",
            payload={"actionRequestId": action_id, "riskLevel": risk_level},
        )
        return self.get_action_request(action_id)

    def get_action_request(self, action_id: str) -> dict[str, Any]:
        """Devuelve la action request por id.

        Raises:
            KeyError: si la action request no existe.
        """
        row = self._query_one("SELECT * FROM action_requests WHERE id = ?", (action_id,))
        if not row:
            raise KeyError(f"Action request not found: {action_id}")
        return row_to_action_request(row)

    def list_action_requests(self, job_id: str | None = None) -> list[dict[str, Any]]:
        """Lista action requests: por job en orden cronológico, o todas de la más reciente a la más antigua."""
        if job_id:
            rows = self._query(
                "SELECT * FROM action_requests WHERE job_id = ? ORDER BY requested_at ASC", (job_id,)
            )
        else:
            rows = self._query("SELECT * FROM action_requests ORDER BY requested_at DESC")
        return [row_to_action_request(row) for row in rows]

    def _pending_actions(self, job_id: str) -> list[dict[str, Any]]:
        return [
            row_to_action_request(row)
            for row in self._query(
                "SELECT * FROM action_requests WHERE job_id = ? AND status = 'pending'",
                (job_id,),
            )
        ]

    def approve_job(self, job_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        """Aprueba el job a nivel global y, si ya no quedan acciones pendientes, lo pasa a `queued`.

        Siempre deja rastro de auditoría `job.approve`; la promoción de estado y el evento
        `job.approved` solo ocurren cuando no hay action requests pendientes y el job estaba
        en `approval_required`. Persiste con la transacción del caller.
        """
        job = self.get_job(job_id)
        audit = self.record_audit(
            project_id=job["projectId"],
            action="job.approve",
            actor=actor,
            target=job_id,
            payload=redact_secrets(
                {"reason": reason, "granularActionsPending": len(self._pending_actions(job_id))}
            ),
        )
        if not self._pending_actions(job_id) and job["status"] == "approval_required":
            self.connection.execute(
                "UPDATE jobs SET status = 'queued', updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
            self.record_event(
                project_id=job["projectId"],
                job_id=job_id,
                event_type="job.approved",
                payload={"reason": reason},
            )
        return {"job": self.get_job(job_id), "auditEvent": audit}

    def approve_action(
        self,
        job_id: str,
        action_id: str,
        *,
        reason: str = "",
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Aprueba una action request, crea su permission grant y promueve el job si procede.

        En la misma transacción del caller: marca la acción `approved`, emite `action.approved`,
        registra auditoría, crea el grant de seguridad (vía `SecurityPolicyRepository`) que
        habilita la ejecución, y pasa el job a `queued` si era la última acción pendiente. Una
        acción expirada se marca `expired` aquí mismo antes de rechazar.

        Raises:
            KeyError: si la acción no pertenece al job.
            ValueError: si la acción ya fue decidida, la razón está vacía, o la acción expiró.
        """
        action = self.get_action_request(action_id)
        if action["jobId"] != job_id:
            raise KeyError(f"Action {action_id} does not belong to job {job_id}")
        if action["status"] != "pending":
            raise ValueError(f"Action request is already {action['status']}.")
        clean_reason = str(redact_secrets(reason or "")).strip()
        if not clean_reason:
            raise ValueError("Approval reason is required.")
        if action.get("expiresAt") and action["expiresAt"] <= utc_now():
            self.connection.execute(
                "UPDATE action_requests SET status = 'expired', decided_at = ?, decided_by = ? WHERE id = ? AND status = 'pending'",
                (utc_now(), "system", action_id),
            )
            raise ValueError("Action request is expired.")
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE action_requests
            SET status = 'approved', reason = ?, decided_at = ?, decided_by = ?
            WHERE id = ?
            """,
            (clean_reason, timestamp, actor, action_id),
        )
        job = self.get_job(job_id)
        self.record_event(
            project_id=job["projectId"],
            job_id=job_id,
            event_type="action.approved",
            payload={"actionRequestId": action_id},
        )
        audit = self.record_audit(
            project_id=job["projectId"],
            action="action.approve",
            actor=actor,
            target=action_id,
            payload=redact_secrets({"jobId": job_id, "reason": clean_reason}),
        )
        permission_grant = SecurityPolicyRepository(self.connection).create_grant_from_action_request(
            action_request=self.get_action_request(action_id),
            reason=clean_reason,
            granted_by=actor,
        )
        self.record_event(
            project_id=job["projectId"],
            job_id=job_id,
            event_type="permission.grant.created",
            payload={"permissionGrantId": permission_grant["id"], "actionRequestId": action_id},
        )
        if job["status"] == "approval_required" and not self._pending_actions(job_id):
            self.connection.execute(
                "UPDATE jobs SET status = 'queued', updated_at = ? WHERE id = ?",
                (timestamp, job_id),
            )
            self.record_event(
                project_id=job["projectId"], job_id=job_id, event_type="job.approved", payload={}
            )
        return {
            "job": self.get_job(job_id),
            "actionRequest": self.get_action_request(action_id),
            "permissionGrant": permission_grant,
            "auditEvent": audit,
        }

    def deny_action(
        self,
        job_id: str,
        action_id: str,
        *,
        reason: str = "",
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Deniega una action request y cancela el job, liberando su lease.

        En la transacción del caller: marca la acción `denied`, pone el job en `cancelled`
        (sin grant alguno), y emite `action.denied` más la auditoría `action.deny`.

        Raises:
            KeyError: si la acción no pertenece al job.
            ValueError: si la acción ya fue decidida o la razón está vacía.
        """
        action = self.get_action_request(action_id)
        if action["jobId"] != job_id:
            raise KeyError(f"Action {action_id} does not belong to job {job_id}")
        if action["status"] != "pending":
            raise ValueError(f"Action request is already {action['status']}.")
        clean_reason = str(redact_secrets(reason or "")).strip()
        if not clean_reason:
            raise ValueError("Rejection reason is required.")
        timestamp = utc_now()
        self.connection.execute(
            """
            UPDATE action_requests
            SET status = 'denied', reason = ?, decided_at = ?, decided_by = ?
            WHERE id = ?
            """,
            (clean_reason, timestamp, actor, action_id),
        )
        self.connection.execute(
            "UPDATE jobs SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (timestamp, job_id),
        )
        job = self.get_job(job_id)
        self.record_event(
            project_id=job["projectId"],
            job_id=job_id,
            event_type="action.denied",
            payload={"actionRequestId": action_id},
        )
        audit = self.record_audit(
            project_id=job["projectId"],
            action="action.deny",
            actor=actor,
            target=action_id,
            payload=redact_secrets({"jobId": job_id, "reason": clean_reason}),
        )
        return {"job": job, "actionRequest": self.get_action_request(action_id), "auditEvent": audit}

    def cancel_job(self, job_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        """Cancela el job, libera su lease y deja evento `job.cancelled` más auditoría."""
        job = self.get_job(job_id)
        self.connection.execute(
            "UPDATE jobs SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), job_id),
        )
        self.record_event(
            project_id=job["projectId"], job_id=job_id, event_type="job.cancelled", payload={"reason": reason}
        )
        audit = self.record_audit(
            project_id=job["projectId"],
            action="job.cancel",
            actor=actor,
            target=job_id,
            payload=redact_secrets({"reason": reason}),
        )
        return {"job": self.get_job(job_id), "auditEvent": audit}

    def retry_job(self, job_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        """Reencola el job liberando su lease; vuelve a `approval_required` si aún tiene acciones pendientes."""
        job = self.get_job(job_id)
        status = "approval_required" if self._pending_actions(job_id) else "queued"
        self.connection.execute(
            "UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (status, utc_now(), job_id),
        )
        self.record_event(
            project_id=job["projectId"], job_id=job_id, event_type="job.retried", payload={"reason": reason}
        )
        audit = self.record_audit(
            project_id=job["projectId"],
            action="job.retry",
            actor=actor,
            target=job_id,
            payload=redact_secrets({"reason": reason}),
        )
        return {"job": self.get_job(job_id), "auditEvent": audit}

    def update_job_status(
        self, job_id: str, *, status: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fija el status del job, fusiona la metadata redactada en `payload.result` y libera el lease.

        Emite un evento `job.<status>` con la metadata redactada; usa la transacción del caller.
        """
        job = self.get_job(job_id)
        payload = dict(job["payload"] or {})
        if metadata is not None:
            payload["result"] = redact_secrets(metadata)
        payload = redact_secrets(payload)
        self.connection.execute(
            """
            UPDATE jobs
            SET status = ?, payload = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (status, json_dumps(payload), utc_now(), job_id),
        )
        self.record_event(
            project_id=job["projectId"],
            job_id=job_id,
            event_type=f"job.{status}",
            payload=redact_secrets(metadata or {}),
        )
        return self.get_job(job_id)

    def claim_next_job(self, *, worker_id: str, lease_ms: int = 300000) -> dict[str, Any] | None:
        """Reclama atómicamente el job `queued` más antiguo, lo pone `running` y abre su run.

        Transacción propia: abre `BEGIN IMMEDIATE` para serializar el reclamo entre workers
        concurrentes y hace COMMIT (o ROLLBACK ante cualquier error) antes de leer el resultado;
        el UPDATE condicionado a `status = 'queued'` evita doble-reclamo. El evento `job.claimed`
        se emite ya fuera de esa transacción.

        Returns:
            Dict con el job y su run reclamados, o `None` si la cola está vacía.
        """
        timestamp = utc_now()
        run_id = f"run-{uuid.uuid4()}"
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                self.connection.execute("ROLLBACK")
                return None
            self.connection.execute(
                """
                UPDATE jobs
                SET status = 'running', lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (worker_id, add_millis(lease_ms), timestamp, row["id"]),
            )
            self.connection.execute(
                """
                INSERT INTO job_runs
                    (id, job_id, provider_id, status, started_at, completed_at, summary, metadata)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (run_id, row["id"], worker_id, "running", timestamp, "", json_dumps({})),
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        job = self.get_job(row["id"])
        run = row_to_job_run(self._query_one("SELECT * FROM job_runs WHERE id = ?", (run_id,)))
        self.record_event(
            project_id=job["projectId"],
            job_id=job["id"],
            event_type="job.claimed",
            payload={"workerId": worker_id},
        )
        return {"job": job, "run": run}

    def requeue_expired_jobs(self, *, now_iso: str | None = None) -> list[dict[str, Any]]:
        """Recupera jobs `running` cuyo lease venció: los reencola y marca su run como `failed`.

        Por cada job vencido emite `job.requeued`; opera sobre la transacción del caller.

        Returns:
            Los jobs recuperados, en el estado `queued` resultante.
        """
        now_value = now_iso or utc_now()
        rows = self._query(
            """
            SELECT * FROM jobs
            WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            ORDER BY updated_at ASC
            """,
            (now_value,),
        )
        recovered: list[dict[str, Any]] = []
        for row in rows:
            self.connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), row["id"]),
            )
            self.connection.execute(
                """
                UPDATE job_runs
                SET status = 'failed', completed_at = ?, summary = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (utc_now(), "Lease expired before completion", row["id"]),
            )
            recovered_job = self.get_job(row["id"])
            recovered.append(recovered_job)
            self.record_event(
                project_id=recovered_job["projectId"],
                job_id=recovered_job["id"],
                event_type="job.requeued",
                payload={"reason": "lease_expired"},
            )
        return recovered

    def complete_job_run(
        self,
        *,
        job_id: str,
        run_id: str,
        status: str,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cierra un run con su resultado y propaga el estado terminal al job, liberando el lease.

        El job queda `completed` solo si el run lo está; cualquier otro status lo deja `failed`.
        Summary y metadata se redactan antes de persistir y de emitir `job.<status>`; usa la
        transacción del caller.
        """
        job_status = "completed" if status == "completed" else "failed"
        timestamp = utc_now()
        clean_summary = str(redact_secrets(summary))
        clean_metadata = redact_secrets(metadata or {})
        self.connection.execute(
            """
            UPDATE job_runs
            SET status = ?, completed_at = ?, summary = ?, metadata = ?
            WHERE id = ?
            """,
            (status, timestamp, clean_summary, json_dumps(clean_metadata), run_id),
        )
        self.connection.execute(
            """
            UPDATE jobs
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (job_status, timestamp, job_id),
        )
        job = self.get_job(job_id)
        self.record_event(
            project_id=job["projectId"],
            job_id=job_id,
            event_type=f"job.{job_status}",
            payload={"summary": clean_summary, "metadata": clean_metadata},
        )
        return {
            "job": job,
            "run": row_to_job_run(self._query_one("SELECT * FROM job_runs WHERE id = ?", (run_id,))),
        }

    def list_job_runs(self, job_id: str | None = None) -> list[dict[str, Any]]:
        """Lista los runs (de un job o globales) en orden cronológico de inicio."""
        if job_id:
            rows = self._query("SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at ASC", (job_id,))
        else:
            rows = self._query("SELECT * FROM job_runs ORDER BY started_at ASC")
        return [row_to_job_run(row) for row in rows]

    def record_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Registra un evento de dominio en el bus, atado opcionalmente a proyecto y job."""
        return EventBus(self.connection).record_event(
            event_type=event_type,
            payload=payload,
            project_id=project_id,
            job_id=job_id,
        )

    def list_events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Devuelve los eventos del bus, filtrando por proyecto cuando se indica."""
        return EventBus(self.connection).list_events(project_id=project_id)

    def record_audit(
        self,
        *,
        action: str,
        target: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Registra una entrada de auditoría (acción de un actor sobre un target) en el bus."""
        return EventBus(self.connection).record_audit(
            action=action,
            target=target,
            payload=payload,
            project_id=project_id,
            actor=actor,
        )

    def list_audit_events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Devuelve las entradas de auditoría, filtrando por proyecto cuando se indica."""
        return EventBus(self.connection).list_audit_events(project_id=project_id)
