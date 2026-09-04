"""Persistencia SQLite de leases, admisiones, muestras y violaciones de recursos.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import iso_after_seconds, utc_now

from .models import (
    ResourceAdmissionDecision,
    ResourceAdmissionRequest,
    ResourceLease,
    ResourceSnapshot,
    ResourceUsageSample,
    ResourceViolation,
    WorkloadProfile,
)


def _lease_from_row(row: sqlite3.Row) -> ResourceLease:
    return ResourceLease.model_validate(
        {
            "id": row["id"],
            "executionId": row["execution_id"],
            "workloadClass": row["workload_class"],
            "ownerId": row["owner_id"],
            "cpuLimitPercent": row["cpu_limit_percent"],
            "memoryLimitBytes": row["memory_limit_bytes"],
            "processLimit": row["process_limit"],
            "gpuRequired": bool(row["gpu_required"]),
            "acquiredAt": row["acquired_at"],
            "heartbeatAt": row["heartbeat_at"],
            "expiresAt": row["expires_at"],
            "releasedAt": row["released_at"],
            "releaseReason": row["release_reason"],
        }
    )


class ResourceRepository:
    """Repositorio de estado operacional; las transacciones de admisión pertenecen al governor."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def active_leases(self, *, now_iso: str | None = None) -> list[ResourceLease]:
        """Lista leases no liberadas y todavía vigentes."""
        now_value = now_iso or utc_now()
        rows = self.connection.execute(
            """
            SELECT * FROM resource_leases
            WHERE released_at IS NULL AND expires_at > ?
            ORDER BY acquired_at ASC, rowid ASC
            """,
            (now_value,),
        ).fetchall()
        return [_lease_from_row(row) for row in rows]

    def active_lease_for_execution(
        self, execution_id: str, *, now_iso: str | None = None
    ) -> ResourceLease | None:
        """Obtiene la reserva vigente e idempotente de una ejecución."""
        now_value = now_iso or utc_now()
        row = self.connection.execute(
            """
            SELECT * FROM resource_leases
            WHERE execution_id = ? AND released_at IS NULL AND expires_at > ?
            ORDER BY acquired_at DESC LIMIT 1
            """,
            (execution_id, now_value),
        ).fetchone()
        return _lease_from_row(row) if row else None

    def create_lease(
        self,
        request: ResourceAdmissionRequest,
        profile: WorkloadProfile,
        *,
        now_iso: str,
    ) -> ResourceLease:
        """Inserta una lease con los límites del perfil admitido."""
        lease_id = f"resource-lease-{uuid.uuid4()}"
        expires_at = iso_after_seconds(now_iso, request.lease_seconds)
        self.connection.execute(
            """
            INSERT INTO resource_leases
                (id, execution_id, workload_class, owner_id, cpu_limit_percent,
                 memory_limit_bytes, process_limit, gpu_required, acquired_at,
                 heartbeat_at, expires_at, released_at, release_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '')
            """,
            (
                lease_id,
                request.execution_id,
                request.workload_class,
                request.owner_id,
                profile.cpu_limit_percent,
                profile.memory_limit_bytes,
                profile.process_limit,
                int(profile.gpu_required),
                now_iso,
                now_iso,
                expires_at,
            ),
        )
        return self.get_lease(lease_id)

    def get_lease(self, lease_id: str) -> ResourceLease:
        """Obtiene una lease por id o falla si no existe."""
        row = self.connection.execute("SELECT * FROM resource_leases WHERE id = ?", (lease_id,)).fetchone()
        if row is None:
            raise KeyError(f"Resource lease not found: {lease_id}")
        return _lease_from_row(row)

    def heartbeat(
        self,
        lease_id: str,
        *,
        owner_id: str,
        lease_seconds: int,
        now_iso: str | None = None,
    ) -> ResourceLease | None:
        """Renueva una lease sólo si sigue activa y pertenece al owner indicado."""
        now_value = now_iso or utc_now()
        cursor = self.connection.execute(
            """
            UPDATE resource_leases
            SET heartbeat_at = ?, expires_at = ?
            WHERE id = ? AND owner_id = ? AND released_at IS NULL AND expires_at > ?
            """,
            (
                now_value,
                iso_after_seconds(now_value, lease_seconds),
                lease_id,
                owner_id,
                now_value,
            ),
        )
        return self.get_lease(lease_id) if cursor.rowcount else None

    def release(
        self,
        lease_id: str,
        *,
        reason: str,
        now_iso: str | None = None,
    ) -> ResourceLease:
        """Libera una lease de forma idempotente, preservando el primer motivo."""
        now_value = now_iso or utc_now()
        self.connection.execute(
            """
            UPDATE resource_leases
            SET released_at = ?, release_reason = ?
            WHERE id = ? AND released_at IS NULL
            """,
            (now_value, reason, lease_id),
        )
        return self.get_lease(lease_id)

    def recover_expired(self, *, now_iso: str | None = None) -> list[ResourceLease]:
        """Marca leases expiradas como recuperadas y devuelve las filas afectadas."""
        now_value = now_iso or utc_now()
        ids = [
            row["id"]
            for row in self.connection.execute(
                """
                SELECT id FROM resource_leases
                WHERE released_at IS NULL AND expires_at <= ?
                ORDER BY expires_at ASC, rowid ASC
                """,
                (now_value,),
            ).fetchall()
        ]
        for lease_id in ids:
            self.release(lease_id, reason="lease_expired", now_iso=now_value)
        return [self.get_lease(lease_id) for lease_id in ids]

    def record_admission(
        self,
        request: ResourceAdmissionRequest,
        decision: ResourceAdmissionDecision,
    ) -> None:
        """Registra cada decisión sin persistir material sensible del proceso."""
        self.connection.execute(
            """
            INSERT INTO resource_admission_decisions
                (id, execution_id, job_id, workload_class, owner_id, status, reason_code,
                 reason, request_json, snapshot_json, lease_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"resource-admission-{uuid.uuid4()}",
                request.execution_id,
                request.job_id,
                request.workload_class,
                request.owner_id,
                decision.status,
                decision.reason_code,
                decision.reason,
                json_dumps(request.model_dump(by_alias=True)),
                json_dumps(decision.snapshot.model_dump(by_alias=True)),
                decision.lease.id if decision.lease else None,
                utc_now(),
            ),
        )

    def waiting_requests(self, *, limit: int = 20) -> list[ResourceAdmissionRequest]:
        """Lista la última solicitud denegada de cada job todavía en resource_wait."""
        rows = self.connection.execute(
            """
            SELECT d.request_json
            FROM resource_admission_decisions d
            JOIN jobs j ON j.id = d.job_id AND j.status = 'resource_wait'
            WHERE d.status = 'resource_wait'
              AND d.rowid = (
                  SELECT MAX(d2.rowid) FROM resource_admission_decisions d2
                  WHERE d2.job_id = d.job_id
              )
            ORDER BY d.created_at ASC, d.rowid ASC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
        return [ResourceAdmissionRequest.model_validate(json_loads(row["request_json"], {})) for row in rows]

    def record_sample(self, snapshot: ResourceSnapshot) -> ResourceUsageSample:
        """Record one bounded host-capacity sample."""
        sample_id = f"resource-sample-{uuid.uuid4()}"
        self.connection.execute(
            "INSERT INTO resource_usage_samples (id, sampled_at, snapshot_json) VALUES (?, ?, ?)",
            (sample_id, snapshot.sampled_at, json_dumps(snapshot.model_dump(by_alias=True))),
        )
        return ResourceUsageSample(id=sample_id, sampled_at=snapshot.sampled_at, snapshot=snapshot)

    def list_samples(self, *, limit: int = 120) -> list[ResourceUsageSample]:
        """Lista muestras nuevas primero con límite defensivo."""
        rows = self.connection.execute(
            """
            SELECT * FROM resource_usage_samples
            ORDER BY sampled_at DESC, rowid DESC LIMIT ?
            """,
            (max(1, min(limit, 1_000)),),
        ).fetchall()
        return [
            ResourceUsageSample(
                id=row["id"],
                sampled_at=row["sampled_at"],
                snapshot=ResourceSnapshot.model_validate(json_loads(row["snapshot_json"], {})),
            )
            for row in rows
        ]

    def latest_sample(self) -> ResourceUsageSample | None:
        """Devuelve la muestra más reciente si el worker ya produjo una."""
        samples = self.list_samples(limit=1)
        return samples[0] if samples else None

    def prune_samples(self, *, retention_seconds: int, now_iso: str | None = None) -> int:
        """Elimina sólo muestras anteriores a la ventana indicada."""
        now = datetime.fromisoformat((now_iso or utc_now()).replace("Z", "+00:00"))
        cutoff = (now - timedelta(seconds=max(1, retention_seconds))).isoformat(timespec="milliseconds")
        cutoff = cutoff.replace("+00:00", "Z")
        cursor = self.connection.execute("DELETE FROM resource_usage_samples WHERE sampled_at < ?", (cutoff,))
        return cursor.rowcount

    def record_violation(
        self,
        *,
        execution_id: str,
        lease_id: str | None,
        violation_type: str,
        action: str,
        reason: str,
    ) -> ResourceViolation:
        """Registra una infracción y la mitigación solicitada."""
        violation = ResourceViolation(
            id=f"resource-violation-{uuid.uuid4()}",
            execution_id=execution_id,
            lease_id=lease_id,
            violation_type=violation_type,
            action=action,
            reason=reason,
            created_at=utc_now(),
        )
        self.connection.execute(
            """
            INSERT INTO resource_violations
                (id, execution_id, lease_id, violation_type, action, reason, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                violation.id,
                violation.execution_id,
                violation.lease_id,
                violation.violation_type,
                violation.action,
                violation.reason,
                violation.created_at,
            ),
        )
        return violation
