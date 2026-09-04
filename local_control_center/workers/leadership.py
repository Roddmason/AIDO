"""Liderazgo y control durable del worker local mediante SQLite y fencing monotónico.

Cada instancia local tiene una única fila de lease. La adquisición, renovación y toma de control
se serializan con ``BEGIN IMMEDIATE``; el token nunca se reutiliza, por lo que un leader vencido no
puede cerrar trabajo después de que un standby tome control.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

LOCAL_WORKER_INSTANCE_ID = "local"
DEFAULT_LEADER_LEASE_SECONDS = 10.0
DEFAULT_HEARTBEAT_STALE_SECONDS = 15.0


def _utc_datetime(value: str | None = None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class WorkerLeadershipDecision:
    """Resultado observable de competir por la lease de liderazgo."""

    acquired: bool
    role: str
    owner_id: str
    fencing_token: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str
    reason: str


class WorkerLeadershipRepository:
    """Opera la lease singleton y los heartbeats sobre una conexión SQLite del caller."""

    def __init__(self, connection: sqlite3.Connection, *, instance_id: str = LOCAL_WORKER_INSTANCE_ID):
        self.connection = connection
        self.instance_id = instance_id

    def acquire(
        self,
        *,
        owner_id: str,
        lease_seconds: float = DEFAULT_LEADER_LEASE_SECONDS,
        now: str | None = None,
    ) -> WorkerLeadershipDecision:
        """Adquiere o renueva liderazgo atómicamente; un competidor vigente queda standby."""
        clean_owner = owner_id.strip()
        if not clean_owner:
            raise ValueError("Worker owner_id is required.")
        now_dt = _utc_datetime(now)
        now_iso = _iso(now_dt)
        expires_at = _iso(now_dt + timedelta(seconds=max(0.1, float(lease_seconds))))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM worker_leader_leases WHERE instance_id = ?",
                (self.instance_id,),
            ).fetchone()
            expired = row is None or str(row["expires_at"]) <= now_iso
            same_owner = row is not None and str(row["owner_id"]) == clean_owner
            if row is None:
                token = 1
                acquired_at = now_iso
                self.connection.execute(
                    """
                    INSERT INTO worker_leader_leases
                        (instance_id, owner_id, acquired_at, heartbeat_at, expires_at, fencing_token)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (self.instance_id, clean_owner, acquired_at, now_iso, expires_at, token),
                )
                acquired = True
            elif expired:
                token = int(row["fencing_token"]) + 1
                acquired_at = now_iso
                self.connection.execute(
                    """
                    UPDATE worker_leader_leases
                    SET owner_id = ?, acquired_at = ?, heartbeat_at = ?, expires_at = ?, fencing_token = ?
                    WHERE instance_id = ?
                    """,
                    (clean_owner, acquired_at, now_iso, expires_at, token, self.instance_id),
                )
                acquired = True
            elif same_owner:
                token = int(row["fencing_token"])
                acquired_at = str(row["acquired_at"])
                self.connection.execute(
                    """
                    UPDATE worker_leader_leases
                    SET heartbeat_at = ?, expires_at = ?
                    WHERE instance_id = ? AND owner_id = ? AND fencing_token = ?
                    """,
                    (now_iso, expires_at, self.instance_id, clean_owner, token),
                )
                acquired = True
            else:
                token = int(row["fencing_token"])
                acquired_at = str(row["acquired_at"])
                expires_at = str(row["expires_at"])
                acquired = False
            role = "leader" if acquired else "standby"
            self._upsert_heartbeat(
                owner_id=clean_owner,
                fencing_token=token if acquired else None,
                role=role,
                status="connected",
                heartbeat_at=now_iso,
                metadata={"leaseExpiresAt": expires_at},
            )
            self.connection.execute("COMMIT")
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        return WorkerLeadershipDecision(
            acquired=acquired,
            role=role,
            owner_id=clean_owner,
            fencing_token=token,
            acquired_at=acquired_at,
            heartbeat_at=now_iso,
            expires_at=expires_at,
            reason="Leadership lease acquired." if acquired else "Another worker owns the active lease.",
        )

    def renew(
        self,
        *,
        owner_id: str,
        fencing_token: int,
        lease_seconds: float = DEFAULT_LEADER_LEASE_SECONDS,
        status: str = "connected",
        now: str | None = None,
    ) -> bool:
        """Renueva sólo si owner, token y lease vigente todavía coinciden."""
        now_dt = _utc_datetime(now)
        now_iso = _iso(now_dt)
        expires_at = _iso(now_dt + timedelta(seconds=max(0.1, float(lease_seconds))))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """
                UPDATE worker_leader_leases
                SET heartbeat_at = ?, expires_at = ?
                WHERE instance_id = ? AND owner_id = ? AND fencing_token = ? AND expires_at > ?
                """,
                (now_iso, expires_at, self.instance_id, owner_id, int(fencing_token), now_iso),
            )
            renewed = cursor.rowcount == 1
            self._upsert_heartbeat(
                owner_id=owner_id,
                fencing_token=int(fencing_token) if renewed else None,
                role="leader" if renewed else "standby",
                status=status if renewed else "fenced",
                heartbeat_at=now_iso,
                metadata={"leaseExpiresAt": expires_at if renewed else None},
            )
            self.connection.execute("COMMIT")
            return renewed
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def release(self, *, owner_id: str, fencing_token: int, reason: str) -> bool:
        """Expira idempotentemente la lease propia sin borrar su token histórico."""
        now = utc_now()
        cursor = self.connection.execute(
            """
            UPDATE worker_leader_leases
            SET heartbeat_at = ?, expires_at = ?
            WHERE instance_id = ? AND owner_id = ? AND fencing_token = ?
            """,
            (now, now, self.instance_id, owner_id, int(fencing_token)),
        )
        self._upsert_heartbeat(
            owner_id=owner_id,
            fencing_token=None,
            role="standby",
            status="stopped",
            heartbeat_at=now,
            metadata={"reason": str(redact_secrets(reason))},
        )
        return cursor.rowcount == 1

    def is_active(self, *, owner_id: str, fencing_token: int, now: str | None = None) -> bool:
        """Comprueba el fence actual sin renovar la lease."""
        row = self.connection.execute(
            """
            SELECT 1 FROM worker_leader_leases
            WHERE instance_id = ? AND owner_id = ? AND fencing_token = ? AND expires_at > ?
            """,
            (self.instance_id, owner_id, int(fencing_token), now or utc_now()),
        ).fetchone()
        return row is not None

    def current(self) -> dict[str, Any] | None:
        """Devuelve la lease singleton sin inferir que una lease vencida siga vigente."""
        row = self.connection.execute(
            "SELECT * FROM worker_leader_leases WHERE instance_id = ?",
            (self.instance_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_heartbeats(self) -> list[dict[str, Any]]:
        """Lista el último heartbeat persistido de cada worker conocido."""
        rows = self.connection.execute(
            "SELECT * FROM worker_heartbeats WHERE instance_id = ? ORDER BY heartbeat_at DESC",
            (self.instance_id,),
        ).fetchall()
        return [
            {
                "ownerId": row["owner_id"],
                "instanceId": row["instance_id"],
                "fencingToken": row["fencing_token"],
                "role": row["role"],
                "status": row["status"],
                "heartbeatAt": row["heartbeat_at"],
                "metadata": json_loads(row["metadata"], {}),
            }
            for row in rows
        ]

    def _upsert_heartbeat(
        self,
        *,
        owner_id: str,
        fencing_token: int | None,
        role: str,
        status: str,
        heartbeat_at: str,
        metadata: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO worker_heartbeats
                (owner_id, instance_id, fencing_token, role, status, heartbeat_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                instance_id = excluded.instance_id,
                fencing_token = excluded.fencing_token,
                role = excluded.role,
                status = excluded.status,
                heartbeat_at = excluded.heartbeat_at,
                metadata = excluded.metadata
            """,
            (
                owner_id,
                self.instance_id,
                fencing_token,
                role,
                status,
                heartbeat_at,
                json_dumps(redact_secrets(metadata)),
            ),
        )


class WorkerControlRepository:
    """Persistencia del estado deseado y comandos de control del worker local."""

    def __init__(self, connection: sqlite3.Connection, *, instance_id: str = LOCAL_WORKER_INSTANCE_ID):
        self.connection = connection
        self.instance_id = instance_id

    def request_state(self, state: str, *, reason: str, requested_by: str = "operator") -> dict[str, Any]:
        """Actualiza pause/resume/drain/stop de forma auditable por el caller."""
        if state not in {"paused", "running", "draining", "stopped", "emergency_stopped"}:
            raise ValueError(f"Unsupported worker state: {state}")
        clean_reason = str(redact_secrets(reason)).strip()
        if state == "emergency_stopped" and not clean_reason:
            raise ValueError("Emergency stop reason is required.")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO worker_control_state
                (instance_id, desired_state, run_once_requested_at, reason, requested_by, updated_at)
            VALUES (?, ?, NULL, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                desired_state = excluded.desired_state,
                reason = excluded.reason,
                requested_by = excluded.requested_by,
                updated_at = excluded.updated_at
            """,
            (self.instance_id, state, clean_reason, requested_by, now),
        )
        return self.get()

    def request_run_once(self, *, reason: str, requested_by: str = "operator") -> dict[str, Any]:
        """Solicita un batch al proceso worker sin ejecutarlo en el proceso HTTP."""
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO worker_control_state
                (instance_id, desired_state, run_once_requested_at, reason, requested_by, updated_at)
            VALUES (?, 'paused', ?, ?, ?, ?)
            ON CONFLICT(instance_id) DO UPDATE SET
                run_once_requested_at = excluded.run_once_requested_at,
                reason = excluded.reason,
                requested_by = excluded.requested_by,
                updated_at = excluded.updated_at
            """,
            (self.instance_id, now, str(redact_secrets(reason)), requested_by, now),
        )
        return self.get()

    def consume_run_once(self, requested_at: str) -> bool:
        """Consume una solicitud concreta para que sólo un leader ejecute ese batch."""
        cursor = self.connection.execute(
            """
            UPDATE worker_control_state
            SET run_once_requested_at = NULL, updated_at = ?
            WHERE instance_id = ? AND run_once_requested_at = ?
            """,
            (utc_now(), self.instance_id, requested_at),
        )
        return cursor.rowcount == 1

    def get(self) -> dict[str, Any]:
        """Devuelve el control durable, creando el default seguro pausado si falta."""
        row = self.connection.execute(
            "SELECT * FROM worker_control_state WHERE instance_id = ?",
            (self.instance_id,),
        ).fetchone()
        if row is None:
            self.request_state("paused", reason="Worker requires an explicit resume.", requested_by="system")
            row = self.connection.execute(
                "SELECT * FROM worker_control_state WHERE instance_id = ?",
                (self.instance_id,),
            ).fetchone()
        return {
            "instanceId": row["instance_id"],
            "desiredState": row["desired_state"],
            "runOnceRequestedAt": row["run_once_requested_at"],
            "reason": row["reason"],
            "requestedBy": row["requested_by"],
            "updatedAt": row["updated_at"],
        }
