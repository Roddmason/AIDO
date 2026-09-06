"""Recupera evidencia y reservas tras perder al owner, verificando identidad antes de actuar.

Nunca termina un PID reutilizado ni un proceso ajeno. Las transacciones sólo abarcan la
persistencia, no las consultas OS ni el cleanup Docker.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import time
from contextlib import closing
from pathlib import Path

import psutil

from local_control_center.evidence.artifacts import evidence_artifact_root
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.diagnostics import diagnostic_event
from local_control_center.shared.serialization import json_dumps

from .models import ProcessStats
from .repository import ManagedProcessRepository


def identity_alive(pid: int, created: float) -> bool:
    """AccessDenied o identidad legacy sin timestamp no autorizan matar ni recuperar."""
    if pid <= 0 or created <= 0:
        return True
    try:
        process = psutil.Process(pid)
        return process.status() != psutil.STATUS_ZOMBIE and abs(process.create_time() - created) < 0.01
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def recover_managed_processes(db_path: Path) -> list[str]:
    """Finaliza registros abandonados sólo después de comprobar que ya no existe su árbol."""
    with closing(open_sqlite_connection(db_path)) as connection:
        records = ManagedProcessRepository(connection).active()
        containers = connection.execute(
            "SELECT * FROM managed_containers WHERE released_at IS NULL"
        ).fetchall()
    from .docker import cleanup_container

    for container in containers:
        if not identity_alive(container["owner_pid"], container["owner_create_time"]):
            cleanup_container(db_path, name=container["name"], cwd=db_path.parent)
    recovered = []
    for record in records:
        if identity_alive(record.owner_pid, record.owner_create_time):
            continue
        if record.root_pid <= 0 or record.root_create_time <= 0:
            diagnostic_event(
                "recovery.identity.unknown",
                component="recovery",
                executionId=record.execution_id,
                managedProcessId=record.managed_process_id,
                outcome="BLOCKED",
                causeStatus="UNKNOWN",
            )
            # Native start resumes before the identity update. Zero is UNKNOWN, not absence.
            # Keep the active record/lease so job recovery cannot replay uncertain external effects.
            logging.getLogger(__name__).warning(
                "process_recovery_unknown_identity managed_process_id=%s execution_id=%s",
                record.managed_process_id,
                record.execution_id,
            )
            continue
        if record.root_create_time > 0 and identity_alive(record.root_pid, record.root_create_time):
            if os.name != "nt":
                # Sólo un grupo cuya raíz conserva la identidad registrada puede terminarse.
                if os.getpgid(record.root_pid) != record.root_pid:
                    raise RuntimeError("La raíz POSIX ya no pertenece al grupo registrado.")
                os.killpg(record.root_pid, signal.SIGKILL)
            # Kill-on-close puede demorar brevemente tras un cierre abrupto de Windows.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and identity_alive(record.root_pid, record.root_create_time):
                time.sleep(0.02)
            if identity_alive(record.root_pid, record.root_create_time):
                raise RuntimeError(
                    "Un árbol AIDO conserva su raíz tras perder al owner; requiere intervención."
                )
        if os.name != "nt":
            from .posix import _group_members

            if _group_members(record.root_pid):
                raise RuntimeError(
                    "Quedan descendientes POSIX sin owner verificable; recuperación bloqueada."
                )
        with closing(open_sqlite_connection(db_path)) as connection:
            for artifact_id in (record.stdout_artifact_id, record.stderr_artifact_id):
                _recover_artifact(connection, db_path, artifact_id)
            ManagedProcessRepository(connection).finish(
                record.managed_process_id,
                stats=ProcessStats(cancelled=True, termination_reason="owner_crashed; partial_evidence"),
            )
            if record.resource_lease_id:
                ResourceRepository(connection).release(record.resource_lease_id, reason="owner_crashed")
        recovered.append(record.managed_process_id)
        diagnostic_event(
            "recovery.closed",
            component="recovery",
            executionId=record.execution_id,
            managedProcessId=record.managed_process_id,
            resourceLeaseId=record.resource_lease_id,
            pid=record.root_pid,
            processCreationTime=record.root_create_time,
            outcome="partial_evidence",
        )
    from local_control_center.agents.runtime_registry import recover_codex_homes

    with closing(open_sqlite_connection(db_path)) as connection:
        recover_codex_homes(connection)
    return recovered


def _recover_artifact(connection, db_path: Path, artifact_id: str | None) -> None:
    if not artifact_id:
        return
    row = connection.execute("SELECT path FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if not row:
        return
    path = Path(row[0]).resolve()
    if not path.is_relative_to(evidence_artifact_root(db_path.parent).resolve()) or not path.is_file():
        return
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    connection.execute(
        "UPDATE artifacts SET hash = ?, metadata = ? WHERE id = ?",
        (
            digest,
            json_dumps({"status": "partial", "reason": "owner_crashed", "sizeBytes": path.stat().st_size}),
            artifact_id,
        ),
    )
