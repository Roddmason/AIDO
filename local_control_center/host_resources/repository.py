"""Persistencia SQLite de leases, admisiones, muestras y violaciones de recursos.

Las transacciones compuestas pertenecen al gobernador; los helpers no mantienen locks externos.

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
from .profiles import admission_memory_bytes


def _lease_from_row(row: sqlite3.Row) -> ResourceLease:
    return ResourceLease.model_validate(
        {
            "id": row["id"],
            "executionId": row["execution_id"],
            "workloadClass": row["workload_class"],
            "ownerId": row["owner_id"],
            "cpuLimitPercent": row["cpu_limit_percent"],
            "memoryLimitBytes": row["memory_limit_bytes"],
            "memoryRequestBytes": row["memory_request_bytes"]
            if "memory_request_bytes" in row.keys()
            else None,
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
            WHERE released_at IS NULL
            ORDER BY acquired_at ASC, rowid ASC
            """,
        ).fetchall()
        held = self._native_lease_ids()
        return [_lease_from_row(row) for row in rows if row["expires_at"] > now_value or row["id"] in held]

    def _native_lease_ids(self) -> set[str]:
        """Una lease vencida no demuestra que terminó el proceso o contenedor que contenía."""
        tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('managed_processes', 'managed_containers')"
            )
        }
        held: set[str] = set()
        if "managed_processes" in tables:
            held.update(
                row[0]
                for row in self.connection.execute(
                    "SELECT resource_lease_id FROM managed_processes WHERE finished_at IS NULL AND resource_lease_id IS NOT NULL"
                )
            )
            held.update(
                row[0]
                for row in self.connection.execute(
                    """SELECT l.id FROM resource_leases l JOIN managed_processes p
                ON p.execution_id=l.parent_execution_id WHERE p.finished_at IS NULL AND l.released_at IS NULL"""
                )
            )
        if "managed_containers" in tables:
            held.update(
                row[0]
                for row in self.connection.execute(
                    "SELECT resource_lease_id FROM managed_containers WHERE released_at IS NULL"
                )
            )
        return held

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
                 memory_limit_bytes, memory_request_bytes, process_limit, gpu_required, acquired_at,
                 heartbeat_at, expires_at, released_at, release_reason, parent_execution_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', ?)
            """,
            (
                lease_id,
                request.execution_id,
                request.workload_class,
                request.owner_id,
                profile.cpu_limit_percent,
                profile.memory_limit_bytes,
                admission_memory_bytes(profile),
                profile.process_limit,
                int(profile.gpu_required),
                now_iso,
                now_iso,
                expires_at,
                request.parent_execution_id,
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
        if lease_id in self._native_lease_ids():
            return self.get_lease(lease_id)
        self.connection.execute(
            """
            UPDATE resource_leases
            SET released_at = ?, release_reason = ?
            WHERE id = ? AND released_at IS NULL
            """,
            (now_value, reason, lease_id),
        )
        # La lease ya no está activa: sus violaciones abiertas dejan de tener efecto (ver ADR-005).
        self.resolve_violations_for_lease(lease_id, now_iso=now_value)
        return self.get_lease(lease_id)

    def finish_remote_branch(self, lease_id: str, *, owner_id: str) -> ResourceLease:
        """Retira el vínculo padre sólo después de retornar la llamada remota en su hilo dueño."""
        self.connection.execute(
            "UPDATE resource_leases SET parent_execution_id=NULL WHERE id=? AND owner_id=? AND released_at IS NULL",
            (lease_id, owner_id),
        )
        return self.release(lease_id, reason="provider_branch_finished")

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
        ids = [lease_id for lease_id in ids if lease_id not in self._native_lease_ids()]
        for lease_id in ids:
            self.release(lease_id, reason="lease_expired", now_iso=now_value)
        return [self.get_lease(lease_id) for lease_id in ids]

    def record_admission(
        self,
        request: ResourceAdmissionRequest,
        decision: ResourceAdmissionDecision,
    ) -> None:
        """Registra cada decisión sin persistir material sensible del proceso."""
        from local_control_center.shared.diagnostics import diagnostic_event

        diagnostic_event(
            "resources.admission",
            component="governor",
            executionId=request.execution_id,
            outcome=decision.status,
            reason=decision.reason_code,
            resourceLeaseId=decision.lease.id if decision.lease else None,
            sample={
                "hostAvailableMemoryBytes": decision.snapshot.available_memory_bytes,
                "hostCpuPercent1s": decision.snapshot.cpu_percent_1s,
            },
        )
        moment = utc_now()
        snapshot_json = json_dumps(decision.snapshot.model_dump(by_alias=True))
        # Un veredicto identico repetido no es informacion nueva: los dos lectores que existen
        # piden la fila mas reciente, asi que insertar otra solo escribe. Se actualiza la que ya
        # esta, conservando `created_at` para no reiniciar el reloj de la espera.
        latest = self.connection.execute(
            """
            SELECT rowid, status, reason_code FROM resource_admission_decisions
            WHERE execution_id = ? ORDER BY rowid DESC LIMIT 1
            """,
            (request.execution_id,),
        ).fetchone()
        if (
            latest is not None
            and latest["status"] == decision.status
            and latest["reason_code"] == decision.reason_code
        ):
            self.connection.execute(
                """
                UPDATE resource_admission_decisions
                SET attempts = attempts + 1, last_seen_at = ?, snapshot_json = ?, lease_id = ?
                WHERE rowid = ?
                """,
                (
                    moment,
                    snapshot_json,
                    decision.lease.id if decision.lease else None,
                    latest["rowid"],
                ),
            )
            return
        self.connection.execute(
            """
            INSERT INTO resource_admission_decisions
                (id, execution_id, job_id, workload_class, owner_id, status, reason_code,
                 reason, request_json, snapshot_json, lease_id, created_at, attempts,
                 last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
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
                snapshot_json,
                decision.lease.id if decision.lease else None,
                moment,
                moment,
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
        from local_control_center.shared.diagnostics import diagnostic_event

        diagnostic_event(
            "host.sample",
            component="governor",
            sample={
                "hostAvailableMemoryBytes": snapshot.available_memory_bytes,
                "hostCpuPercent1s": snapshot.cpu_percent_1s,
                "hostCpuPercent30s": snapshot.cpu_percent_30s,
            },
        )
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

    def prune_admission_decisions(
        self, *, retention_seconds: int, now_iso: str | None = None, batch_size: int | None = None
    ) -> int:
        """Elimina decisiones de admision anteriores a la ventana indicada.

        Cada intento de admision escribia una fila permanente con su `request_json` y su
        `snapshot_json` completos: 130.770 filas / 119,3 MB medidos en la instalacion real, el
        16% de toda la base. El mecanismo de retencion ya existia y solo estaba cableado a las
        muestras de capacidad.

        Con ``batch_size`` borra como maximo ese lote, empezando por el ``rowid`` mas bajo: la tabla
        es de insercion, asi que las filas viejas estan al principio y la subconsulta se detiene al
        llenar el lote aunque no haya indice por fecha.
        """
        now = datetime.fromisoformat((now_iso or utc_now()).replace("Z", "+00:00"))
        cutoff = (now - timedelta(seconds=max(1, retention_seconds))).isoformat(timespec="milliseconds")
        cutoff = cutoff.replace("+00:00", "Z")
        if batch_size is None:
            cursor = self.connection.execute(
                "DELETE FROM resource_admission_decisions WHERE created_at < ?", (cutoff,)
            )
        else:
            cursor = self.connection.execute(
                "DELETE FROM resource_admission_decisions WHERE rowid IN ("
                " SELECT rowid FROM resource_admission_decisions WHERE created_at < ? ORDER BY rowid LIMIT ?)",
                (cutoff, max(1, batch_size)),
            )
        return cursor.rowcount

    def record_violation(
        self,
        *,
        execution_id: str,
        lease_id: str,
        violation_type: str,
        action: str,
        reason: str,
        now_iso: str | None = None,
    ) -> ResourceViolation:
        """Registra una infracción y la mitigación solicitada.

        Siempre ligada a una lease: `cancellation_reason` solo ve violaciones de leases activas, así que
        una violación sin lease quedaría invisible y nunca cancelaría nada.
        """
        if not lease_id:
            raise ValueError("A resource violation must reference the lease it evicts.")
        violation = ResourceViolation(
            id=f"resource-violation-{uuid.uuid4()}",
            execution_id=execution_id,
            lease_id=lease_id,
            violation_type=violation_type,
            action=action,
            reason=reason,
            created_at=now_iso or utc_now(),
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

    def latest_violation_created_at(self, *, violation_type: str) -> str | None:
        """Momento de la violación más reciente de un tipo, para calibrar la gracia entre desalojos."""
        row = self.connection.execute(
            """
            SELECT created_at FROM resource_violations
            WHERE violation_type = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (violation_type,),
        ).fetchone()
        return str(row[0]) if row else None

    def leases_with_unresolved_violation(self, *, action: str) -> set[str]:
        """Leases que ya tienen una violación pendiente de esa acción, para no repetirla.

        Por lease, no por ``execution_id``: una violación es un evento de ESA reserva. Si se
        indexara por ejecución, una lease que nunca muere excluiría para siempre cualquier
        ejecución futura con el mismo id (un reintento quedaría cancelado sin remedio); ver
        ADR-005.
        """
        rows = self.connection.execute(
            "SELECT DISTINCT lease_id FROM resource_violations WHERE resolved_at IS NULL AND action = ?",
            (action,),
        ).fetchall()
        return {row[0] for row in rows if row[0] is not None}

    def resolve_violations_for_lease(self, lease_id: str, *, now_iso: str | None = None) -> int:
        """Cierra las violaciones abiertas de una lease cuando deja de estar activa.

        Se llama desde ``release`` (y por lo tanto desde la recuperación de expiradas, que libera
        cada lease vencida) sólo cuando la liberación realmente ocurre. Sin esto, una violación
        `hard_memory_floor` seguía marcando `cancellation_reason` para el ``execution_id`` de esa
        lease incluso después de liberarse, cancelando cualquier reintento futuro con el mismo id.
        """
        cursor = self.connection.execute(
            "UPDATE resource_violations SET resolved_at = ? WHERE lease_id = ? AND resolved_at IS NULL",
            (now_iso or utc_now(), lease_id),
        )
        return cursor.rowcount
