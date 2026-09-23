"""Repositorio SQLite del slice de jobs/aprobaciones: ciclo de vida del job, runs y action requests.

Transacciones: la mayoría de los métodos emiten varios `UPDATE/INSERT` (cambio de estado +
evento + auditoría) y delegan el commit en la transacción del `connection` del caller (modo
autocommit del wrapper). La excepción es `claim_next_job`, que abre `BEGIN IMMEDIATE` y hace
COMMIT/ROLLBACK propios para serializar el reclamo de la cola entre workers concurrentes.
Invariante de seguridad: todo command/payload/reason se redacta con `redact_secrets` antes de
persistir o registrar, de modo que ningún secreto llega a `jobs`, `action_requests` ni eventos.

@author Rodrigo Mason
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

NESTED_AGENT_JOB_KINDS = (
    "agent.product_owner",
    "agent.architect",
    "agent.devops",
    "agent.developer",
    "agent.project_assessment",
)
"""Kinds de agentes anidados que se crean `running` sin lease dentro del job del worker."""


def _current_parent_job_id() -> str | None:
    """Devuelve el execution id del contexto actual, candidato a job padre de un job hijo inline."""
    from local_control_center.process_supervision.context import CURRENT_EXECUTION

    context = CURRENT_EXECUTION.get()
    return context.execution_id if context is not None and context.execution_id else None


class StaleWorkerFenceError(RuntimeError):
    """Indica que un worker perdió liderazgo y ya no puede mutar el run reclamado."""


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
        "workerOwnerId": row["worker_owner_id"] if "worker_owner_id" in row.keys() else None,
        "leaderFencingToken": (row["leader_fencing_token"] if "leader_fencing_token" in row.keys() else None),
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

        Un job creado ya `running` dentro de la ejecución de otro job recibe `parentJobId`, pero sólo
        si ese id existe en `jobs`: `connection_execution_scope` también puede traer un agent_run_id,
        y enlazarlo haría que el reaper viera como huérfano a un hijo vivo.

        Returns:
            Dict con el job creado, los eventos emitidos y las action requests generadas.
        """
        payload = redact_secrets(payload or {})
        from local_control_center.shared.diagnostics import context_fields

        # Override untrusted payload correlation with the server's HTTP context.
        payload = {
            **payload,
            "diagnosticContext": {k: v for k, v in context_fields().items() if k == "requestId"},
        }
        timestamp = utc_now()
        job_id = f"job-{uuid.uuid4()}"
        needs_approval = kind in SENSITIVE_JOB_KINDS or payload.get("approvalRequired") is True
        resolved_status = status or ("approval_required" if needs_approval else "queued")
        parent_job_id = _current_parent_job_id()
        if (
            resolved_status == "running"
            and parent_job_id
            and "parentJobId" not in payload
            and self._query_one("SELECT 1 FROM jobs WHERE id = ?", (parent_job_id,)) is not None
        ):
            payload = {**payload, "parentJobId": parent_job_id}
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

    def list_jobs(self, project_id: str | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Lista los jobs (todos o de un proyecto) ordenados del más reciente al más antiguo.

        ``limit`` acota a los N más recientes con desempate determinista por rowid.
        """
        order = (
            "ORDER BY created_at DESC, rowid DESC LIMIT ?"
            if limit is not None
            else "ORDER BY created_at DESC"
        )
        if project_id:
            params = (project_id, int(limit)) if limit is not None else (project_id,)
            rows = self._query(f"SELECT * FROM jobs WHERE project_id = ? {order}", params)
        else:
            params = (int(limit),) if limit is not None else ()
            rows = self._query(f"SELECT * FROM jobs {order}", params)
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
        from local_control_center.shared.command_privacy import private_command_evidence

        raw_argv = (
            command_argv
            or (payload or {}).get("commandArgv")
            or (payload or {}).get("argv")
            or clean_payload["commandArgv"]
        )
        clean_command, _, clean_payload = private_command_evidence(command, raw_argv, clean_payload)
        clean_command = str(redact_secrets(clean_command))
        clean_payload = redact_secrets(clean_payload)
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

    def list_action_requests(
        self, job_id: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Lista action requests: por job en orden cronológico, o todas de la más reciente a la más antigua.

        ``limit`` acota a las N más recientes preservando el orden de cada rama.
        """
        if job_id:
            if limit is None:
                rows = self._query(
                    "SELECT * FROM action_requests WHERE job_id = ? ORDER BY requested_at ASC", (job_id,)
                )
            else:
                # Tail: trae las N más recientes en DESC determinista y las invierte a ASC.
                rows = list(
                    reversed(
                        self._query(
                            "SELECT * FROM action_requests WHERE job_id = ?"
                            " ORDER BY requested_at DESC, rowid DESC LIMIT ?",
                            (job_id, int(limit)),
                        )
                    )
                )
        elif limit is None:
            rows = self._query("SELECT * FROM action_requests ORDER BY requested_at DESC")
        else:
            rows = self._query(
                "SELECT * FROM action_requests ORDER BY requested_at DESC, rowid DESC LIMIT ?",
                (int(limit),),
            )
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
        if action["actionType"] == "agent.developer.approve_patch":
            return self._decide_developer_patch(
                job_id, action_id, reason=clean_reason, actor=actor, accepted=True
            )
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

    def _decide_developer_patch(
        self, job_id: str, action_id: str, *, reason: str, actor: str, accepted: bool
    ) -> dict[str, Any]:
        """Decide an already executed patch atomically, without granting another execution."""
        from contextlib import nullcontext

        from local_control_center.agents.repository import AgentsRepository
        from local_control_center.evidence.quality import (
            evidence_package_contract_errors,
            real_qa_command_errors,
        )
        from local_control_center.evidence.repository import EvidenceRepository
        from local_control_center.shared.db import immediate_transaction

        transaction = (
            nullcontext() if self.connection.in_transaction else immediate_transaction(self.connection)
        )
        with transaction:
            action = self.get_action_request(action_id)
            job = self.get_job(job_id)
            if action["jobId"] != job_id or action["status"] != "pending":
                raise ValueError("Patch approval is no longer pending for this job.")
            if job["kind"] != "agent.developer" or job["status"] != "approval_required":
                raise ValueError("Patch approval requires an existing DeveloperAgent delivery.")
            if accepted and len(self._pending_actions(job_id)) != 1:
                raise ValueError("Other pending actions must be resolved before accepting this patch.")
            payload = action.get("payload") or {}
            agents = AgentsRepository(self.connection)
            evidence_repo = EvidenceRepository(self.connection)
            try:
                run = agents.get_agent_run(str(payload.get("agentRunId") or ""))
                evidence = evidence_repo.get_evidence_package(str(payload.get("evidencePackageId") or ""))
            except KeyError as error:
                raise ValueError("Patch approval is missing its execution or evidence.") from error
            if (
                run["jobId"] != job_id
                or run["projectId"] != job["projectId"]
                or run["status"] != "awaiting_permission"
                or evidence["jobId"] != job_id
                or evidence["agentRunId"] != run["id"]
                or evidence["projectId"] != job["projectId"]
                or evidence["workspaceId"] != payload.get("workspaceId")
            ):
                raise ValueError("Patch approval execution/evidence scope does not match.")
            if accepted:
                runtime = run["output"].get("runtimeResult") or {}
                if runtime.get("status") != "completed" or runtime.get("returnCode") != 0:
                    raise ValueError("Patch approval requires successful native execution.")
                errors = evidence_package_contract_errors(
                    evidence,
                    require_runtime_links=True,
                    require_workflow_run=bool(evidence.get("workflowRunId")),
                ) + real_qa_command_errors(evidence)
                patch_id = (payload.get("diffSummary") or {}).get("patchArtifactId")
                if not patch_id or patch_id not in (evidence.get("hashes") or {}):
                    errors.append("Patch approval requires its hashed diff artifact.")
                if errors:
                    raise ValueError("Patch acceptance evidence is invalid: " + " ".join(errors))
            now = utc_now()
            status = "completed" if accepted else "cancelled"
            decision = "approved" if accepted else "denied"
            self.connection.execute(
                "UPDATE action_requests SET status=?,reason=?,decided_at=?,decided_by=? WHERE id=?",
                (decision, reason, now, actor, action_id),
            )
            approval = {
                "actionRequestId": action_id,
                "actor": actor,
                "reason": reason,
                "decidedAt": now,
                "status": decision,
            }
            agents.update_agent_run_status(
                run["id"],
                status=status,
                output_payload={**run["output"], "verdict": status, "approval": approval},
            )
            evidence_repo.update_evidence_links(
                evidence["id"],
                qa_verdict="passed" if accepted else None,
                evidence_source="verified_completion" if accepted else None,
                approvals=[*(evidence.get("approvals") or []), self.get_action_request(action_id)],
            )
            self.update_job_status(job_id, status=status, metadata={"status": status, "approval": approval})
            self.record_event(
                project_id=job["projectId"], job_id=job_id, event_type=f"action.{decision}", payload=approval
            )
            audit = self.record_audit(
                project_id=job["projectId"],
                action="action.approve" if accepted else "action.deny",
                actor=actor,
                target=action_id,
                payload={
                    "jobId": job_id,
                    "reason": reason,
                    "effect": "decide_existing_patch_without_reexecution",
                },
            )
            return {
                "job": self.get_job(job_id),
                "actionRequest": self.get_action_request(action_id),
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
        if action["actionType"] == "agent.developer.approve_patch":
            return self._decide_developer_patch(
                job_id, action_id, reason=clean_reason, actor=actor, accepted=False
            )
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
        from local_control_center.process_supervision.repository import ManagedProcessRepository

        ManagedProcessRepository(self.connection).request_execution_cancel(
            job_id, reason=reason or "cancelled_by_operator"
        )
        self.connection.execute(
            "UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE id = ?",
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
        active = self.connection.execute(
            "SELECT 1 FROM managed_processes WHERE execution_id = ? AND finished_at IS NULL LIMIT 1",
            (job_id,),
        ).fetchone()
        if active or (
            job.get("leaseOwner") and job.get("leaseExpiresAt") and job["leaseExpiresAt"] > utc_now()
        ):
            raise ValueError("La ejecución anterior todavía no ha liberado sus procesos y leases.")
        self.connection.execute("DELETE FROM process_execution_controls WHERE execution_id = ?", (job_id,))
        if job["kind"] in {"operation.execute", "thread.product_loop.run", "thread.research.run"}:
            self.connection.execute(
                """UPDATE operational_executions SET status='queued', started_at=NULL,
                finished_at=NULL, cancel_requested_at=NULL, reason='', result_json=NULL,
                owner_id=NULL, fencing_token=NULL WHERE id=?""",
                (job_id,),
            )
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

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_ms: int = 300000,
        leader_fencing_token: int | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
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
            if leader_fencing_token is not None and not self._worker_fence_is_active(
                worker_id=worker_id,
                leader_fencing_token=leader_fencing_token,
                now_iso=timestamp,
            ):
                raise StaleWorkerFenceError("Worker leadership fence is not active.")
            if job_id is None:
                row = self.connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT * FROM jobs WHERE id = ? AND status = 'queued'",
                    (job_id,),
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
                    (id, job_id, provider_id, status, started_at, completed_at, summary, metadata,
                     worker_owner_id, leader_fencing_token)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    row["id"],
                    worker_id,
                    "running",
                    timestamp,
                    "",
                    json_dumps({}),
                    worker_id,
                    leader_fencing_token,
                ),
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

    def peek_next_job(self) -> dict[str, Any] | None:
        """Prioriza operaciones ligeras de reparación; conserva FIFO para el resto de jobs."""
        row = self.connection.execute(
            """
            SELECT j.* FROM jobs j
            LEFT JOIN operational_executions e ON e.id = j.id
            WHERE j.status = 'queued'
            ORDER BY CASE WHEN j.kind = 'operation.execute'
                AND e.workload_class IN ('control_plane', 'qa_light', 'remote_llm_light')
                THEN 0 ELSE 1 END, j.created_at ASC, j.rowid ASC
            LIMIT 1
            """
        ).fetchone()
        return row_to_job(row) if row else None

    def peek_oldest_queued_job(self, kinds: tuple[str, ...]) -> dict[str, Any] | None:
        """Devuelve el job encolado más antiguo de esos tipos, ignorando la prioridad de reparación."""
        placeholders = ",".join("?" for _ in kinds)
        row = self.connection.execute(
            f"""
            SELECT * FROM jobs
            WHERE status = 'queued' AND kind IN ({placeholders})
            ORDER BY created_at ASC, rowid ASC
            LIMIT 1
            """,
            kinds,
        ).fetchone()
        return row_to_job(row) if row else None

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
              AND NOT EXISTS (
                  SELECT 1 FROM managed_processes p
                  WHERE p.execution_id = jobs.id AND (p.finished_at IS NULL OR p.released_at IS NULL)
              )
            ORDER BY updated_at ASC
            """,
            (now_value,),
        )
        recovered: list[dict[str, Any]] = []
        for row in rows:
            if row["kind"] in {"operation.execute", "thread.product_loop.run", "thread.research.run"}:
                execution = self.connection.execute(
                    "SELECT status FROM operational_executions WHERE id=?", (row["id"],)
                ).fetchone()
                if execution and execution["status"] != "queued":
                    terminal_status = (
                        execution["status"] if execution["status"] in {"completed", "cancelled"} else "failed"
                    )
                    if execution["status"] not in {
                        "completed",
                        "failed",
                        "blocked",
                        "cancelled",
                        "interrupted",
                    }:
                        self.connection.execute(
                            """UPDATE operational_executions SET status='interrupted',
                            finished_at=?, reason='Worker lost: external effects require review before retry.' WHERE id=?""",
                            (utc_now(), row["id"]),
                        )
                    self.connection.execute(
                        "UPDATE jobs SET status=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                        (
                            terminal_status,
                            utc_now(),
                            row["id"],
                        ),
                    )
                    self.connection.execute(
                        "UPDATE job_runs SET status=?, completed_at=?, summary='Recovered durable execution outcome after worker lease expiry.' WHERE job_id=? AND status='running'",
                        (terminal_status, utc_now(), row["id"]),
                    )
                    self.record_event(
                        project_id=row["project_id"],
                        job_id=row["id"],
                        event_type="job.recovered",
                        payload={"status": terminal_status, "reason": "lease_expired_no_replay"},
                    )
                    recovered.append(self.get_job(row["id"]))
                    continue
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
        self.fail_orphaned_child_jobs(now_iso=now_value)
        return recovered

    def fail_orphaned_child_jobs(
        self, *, parent_job_id: str | None = None, now_iso: str | None = None
    ) -> list[str]:
        """Falla los jobs hijos inline que quedaron `running` sin lease cuando su padre ya no corre.

        Un hijo creado `running` dentro de otro job no tiene lease propio y ``requeue_expired_jobs``
        nunca lo ve. Sólo se actúa con vínculo comprobable: si su ``parentJobId`` apunta a un job que
        ya no está `running` (terminó, se reencoló o no existe), el hijo es un zombi y se marca
        `failed` con motivo `parent_lease_expired`, cerrando sus `agent_runs` y `job_runs`. Un hijo
        sin ``parentJobId`` nunca se falla por antigüedad (``workflow_run_id`` identifica el product
        loop, no el job padre); el saneamiento histórico va por ``fail_legacy_orphan_child_jobs``.
        Usa la transacción del caller.
        """
        now_value = now_iso or utc_now()
        placeholders = ",".join("?" for _ in NESTED_AGENT_JOB_KINDS)
        rows = self.connection.execute(
            f"""
            SELECT child.id FROM jobs child
            LEFT JOIN jobs parent ON parent.id = json_extract(child.payload, '$.parentJobId')
            WHERE child.status = 'running'
              AND child.lease_expires_at IS NULL
              AND child.kind IN ({placeholders})
              AND json_extract(child.payload, '$.parentJobId') IS NOT NULL
              AND (? IS NULL OR json_extract(child.payload, '$.parentJobId') = ?)
              AND (parent.id IS NULL OR parent.status <> 'running')
            ORDER BY child.created_at ASC, child.rowid ASC
            """,
            (*NESTED_AGENT_JOB_KINDS, parent_job_id, parent_job_id),
        ).fetchall()
        return [self._fail_orphan_child(str(row["id"]), now_value) for row in rows]

    def fail_legacy_orphan_child_jobs(
        self, job_ids: Iterable[str], *, now_iso: str | None = None
    ) -> list[str]:
        """Saneamiento histórico: falla hijos zombi anteriores a ``parentJobId`` por id explícito.

        Cada id sólo se falla si sigue `running`, sin lease, con kind anidado y sin ``parentJobId``;
        cualquier otro (inexistente, terminado, con padre vivo o con vínculo) se ignora. El operador
        entrega los ids tras inspeccionarlos (Task 15); nunca se invoca con una consulta amplia.
        """
        requested = [str(job_id) for job_id in job_ids if str(job_id).strip()]
        if not requested:
            return []
        now_value = now_iso or utc_now()
        kind_placeholders = ",".join("?" for _ in NESTED_AGENT_JOB_KINDS)
        id_placeholders = ",".join("?" for _ in requested)
        rows = self.connection.execute(
            f"""
            SELECT id FROM jobs
            WHERE id IN ({id_placeholders})
              AND status = 'running'
              AND lease_expires_at IS NULL
              AND kind IN ({kind_placeholders})
              AND json_extract(payload, '$.parentJobId') IS NULL
            ORDER BY created_at ASC, rowid ASC
            """,
            (*requested, *NESTED_AGENT_JOB_KINDS),
        ).fetchall()
        return [self._fail_orphan_child(str(row["id"]), now_value) for row in rows]

    def _fail_orphan_child(self, child_id: str, now_value: str) -> str:
        """Marca el hijo `failed` por `parent_lease_expired` y cierra sus `agent_runs`/`job_runs` abiertos."""
        self.update_job_status(child_id, status="failed", metadata={"reason": "parent_lease_expired"})
        self.connection.execute(
            "UPDATE agent_runs SET status = 'failed', updated_at = ? WHERE job_id = ? AND status = 'running'",
            (now_value, child_id),
        )
        self.connection.execute(
            """
            UPDATE job_runs SET status = 'failed', completed_at = ?, summary = 'parent_lease_expired'
            WHERE job_id = ? AND status = 'running'
            """,
            (now_value, child_id),
        )
        return child_id

    def complete_job_run(
        self,
        *,
        job_id: str,
        run_id: str,
        status: str,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
        worker_id: str | None = None,
        leader_fencing_token: int | None = None,
    ) -> dict[str, Any]:
        """Cierra un run con su resultado y propaga el estado terminal al job, liberando el lease.

        El job queda `completed` solo si el run lo está; cualquier otro status lo deja `failed`.
        Summary y metadata se redactan antes de persistir y de emitir `job.<status>`; usa la
        transacción del caller. `cancelled` es terminal: un worker que termina tarde no lo reescribe.
        """
        fenced = worker_id is not None or leader_fencing_token is not None
        if fenced and (not worker_id or leader_fencing_token is None):
            raise ValueError("worker_id and leader_fencing_token must be provided together.")
        owns_transaction = not self.connection.in_transaction
        if owns_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            if fenced and not self._worker_fence_is_active(
                worker_id=str(worker_id),
                leader_fencing_token=int(leader_fencing_token),
                now_iso=utc_now(),
            ):
                raise StaleWorkerFenceError("Worker leadership fence is no longer active.")
            claimed_job = self.get_job(job_id)
            if fenced and claimed_job.get("leaseOwner") != worker_id:
                raise StaleWorkerFenceError("Job lease is no longer owned by this worker.")
            run_row = self._query_one("SELECT * FROM job_runs WHERE id = ? AND job_id = ?", (run_id, job_id))
            if not run_row:
                raise KeyError(f"Job run not found: {run_id}")
            if fenced and (
                run_row["worker_owner_id"] != worker_id
                or run_row["leader_fencing_token"] != leader_fencing_token
            ):
                raise StaleWorkerFenceError("Job run fence does not match the active worker.")
            result = self._complete_job_run_in_transaction(
                job_id=job_id,
                run_id=run_id,
                status=status,
                summary=summary,
                metadata=metadata,
            )
            if owns_transaction:
                self.connection.execute("COMMIT")
            return result
        except Exception:
            if owns_transaction and self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _complete_job_run_in_transaction(
        self,
        *,
        job_id: str,
        run_id: str,
        status: str,
        summary: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Cierra un run dentro de la transacción ya validada por ``complete_job_run``."""
        job_status = status if status in {"completed", "cancelled"} else "failed"
        # A cancel the operator already issued is the truth: keep it, or the audit trail would claim the
        # stopped job finished and `_thread_job_was_cancelled` would stop seeing the cancellation.
        if self.get_job(job_id)["status"] == "cancelled":
            job_status = "cancelled"
            status = "cancelled"
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
        self.fail_orphaned_child_jobs(parent_job_id=job_id, now_iso=timestamp)
        return {
            "job": job,
            "run": row_to_job_run(self._query_one("SELECT * FROM job_runs WHERE id = ?", (run_id,))),
        }

    def heartbeat_job_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        leader_fencing_token: int,
        lease_ms: int,
    ) -> bool:
        """Renueva una lease de job sólo bajo el fence de liderazgo vigente."""
        now = utc_now()
        if not self._worker_fence_is_active(
            worker_id=worker_id,
            leader_fencing_token=leader_fencing_token,
            now_iso=now,
        ):
            return False
        cursor = self.connection.execute(
            """
            UPDATE jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
            """,
            (add_millis(lease_ms), now, job_id, worker_id),
        )
        return cursor.rowcount == 1

    def _worker_fence_is_active(
        self,
        *,
        worker_id: str,
        leader_fencing_token: int,
        now_iso: str,
    ) -> bool:
        return (
            self.connection.execute(
                """
                SELECT 1 FROM worker_leader_leases
                WHERE instance_id = 'local' AND owner_id = ? AND fencing_token = ? AND expires_at > ?
                """,
                (worker_id, int(leader_fencing_token), now_iso),
            ).fetchone()
            is not None
        )

    def list_job_runs(self, job_id: str | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Lista los runs (de un job o globales) en orden cronológico de inicio.

        ``limit`` conserva el tail más reciente sin alterar el orden ascendente final.
        """
        where = "WHERE job_id = ?" if job_id else ""
        base_params: tuple[Any, ...] = (job_id,) if job_id else ()
        if limit is None:
            rows = self._query(f"SELECT * FROM job_runs {where} ORDER BY started_at ASC", base_params)
        else:
            # Tail: trae los N más recientes en DESC determinista y los invierte a ASC.
            rows = list(
                reversed(
                    self._query(
                        f"SELECT * FROM job_runs {where} ORDER BY started_at DESC, rowid DESC LIMIT ?",
                        (*base_params, int(limit)),
                    )
                )
            )
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
        try:
            return EventBus(self.connection).record_event(
                event_type=event_type,
                payload=payload,
                project_id=project_id,
                job_id=job_id,
            )
        except Exception as error:
            return {
                "id": "",
                "jobId": job_id,
                "projectId": project_id,
                "type": event_type,
                "severity": "warning",
                "payload": redact_secrets(
                    {
                        "eventPersistenceFailed": True,
                        "eventType": event_type,
                        "reason": str(error),
                        "payload": payload or {},
                    }
                ),
                "createdAt": utc_now(),
            }

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
