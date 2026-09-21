"""Admisión por rama de inferencia, compartida con la reserva durable de su ejecución padre.

Una rama puede ocupar el slot ya reservado por el job; las demás requieren reservas propias.
Cada hilo usa su conexión y ninguna espera mantiene abierta una transacción SQLite.

@author Rodrigo Mason
"""

from __future__ import annotations

import threading
import time
import weakref
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from local_control_center.process_supervision.context import (
    CURRENT_EXECUTION,
    ProcessExecutionContext,
    execution_scope,
)
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.shared.db import open_sqlite_connection

from .governor import HostResourceGovernor
from .models import ResourceAdmissionRequest
from .probes import HostResourceProbe
from .profiles import CAPTURE_SESSION_PARTS, workload_profile
from .repository import ResourceRepository

_BORROW_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_BORROW_REGISTRY_LOCK = threading.Lock()


class BranchAdmissionDeferred(RuntimeError):
    """A bounded caller can report the governor's reason without waiting."""


class BranchAdmission:
    """Comparte sólo una reserva padre por vez y respeta los límites de otras ejecuciones."""

    def __init__(self, db_path: Path, *, snapshot_source=None):
        self.db_path = db_path
        self.parent = CURRENT_EXECUTION.get()
        key = (str(db_path.resolve()), self.parent.resource_lease_id if self.parent else None)
        with _BORROW_REGISTRY_LOCK:
            self.borrowed = _BORROW_LOCKS.setdefault(key, threading.Lock())
        self.snapshot_source = snapshot_source

    def _verified_parent_lease(self, connection):
        if self.parent is None:
            return None
        if Path(self.parent.db_path).resolve() != self.db_path.resolve():
            raise BranchAdmissionDeferred("resource_parent_database_mismatch")
        if self.parent.fencing_token is not None:
            from local_control_center.executions.repository import ExecutionRepository
            from local_control_center.jobs_approvals.repository import StaleWorkerFenceError

            try:
                ExecutionRepository(connection).require_fence(
                    self.parent.execution_id,
                    owner_id=self.parent.worker_id,
                    fencing_token=self.parent.fencing_token,
                )
            except StaleWorkerFenceError as error:
                raise BranchAdmissionDeferred("resource_parent_fence_lost") from error
        if self.parent.execution_id and ManagedProcessRepository(connection).cancellation_reason(
            self.parent.execution_id
        ):
            raise BranchAdmissionDeferred("execution_cancelled")
        if not self.parent.resource_lease_id:
            return None
        try:
            lease = ResourceRepository(connection).get_lease(self.parent.resource_lease_id)
        except KeyError as error:
            raise BranchAdmissionDeferred("resource_parent_lease_missing") from error
        if lease.released_at or datetime.fromisoformat(
            lease.expires_at.replace("Z", "+00:00")
        ) <= datetime.now(UTC):
            raise BranchAdmissionDeferred("resource_parent_lease_inactive")
        if lease.workload_class == "capture_session":
            from local_control_center.process_supervision.session_client import session_identity

            try:
                session = session_identity(self.db_path)
            except (OSError, ValueError, RuntimeError) as error:
                raise BranchAdmissionDeferred("resource_parent_session_unverified") from error
            if (
                not session
                or session[1].id != lease.id
                or session[0].get("aggregateId") != self.parent.aggregate_managed_process_id
                or not self.parent.aggregate_managed_process_id
                or self.parent.session_role != "execution"
                or not self.parent.in_job_runner
                or not self.parent.execution_id
                or not self.parent.worker_id
            ):
                raise BranchAdmissionDeferred("resource_parent_session_mismatch")
            if ManagedProcessRepository(connection).cancellation_reason(lease.execution_id):
                raise BranchAdmissionDeferred("execution_cancelled")
            return lease
        if (
            not self.parent.execution_id
            or not self.parent.worker_id
            or lease.execution_id != self.parent.execution_id
            or lease.owner_id != self.parent.worker_id
            or self.parent.aggregate_managed_process_id
        ):
            raise BranchAdmissionDeferred("resource_parent_identity_mismatch")
        return lease

    @staticmethod
    def _parent_covers(lease, workload_class):
        profile = workload_profile(workload_class)
        if lease.workload_class == "capture_session":
            part = CAPTURE_SESSION_PARTS["execution"]
            if (
                profile.cpu_limit_percent > part["cpuPercent"]
                or profile.memory_limit_bytes > part["memoryBytes"]
            ):
                return False
        return (
            (
                not profile.gpu_required
                or (lease.workload_class == workload_class == "local_gpu_model" and lease.gpu_required)
            )
            and lease.cpu_limit_percent >= profile.cpu_limit_percent
            and lease.memory_limit_bytes >= profile.memory_limit_bytes
            and lease.process_limit >= profile.process_limit
        )

    @contextmanager
    def reserve(self, branch_id: str, workload_class: str, *, timeout_seconds: float = 900):
        """Espera capacidad con polling cancelable; renueva y libera únicamente las leases propias."""
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        owner_id = self.parent.worker_id if self.parent and self.parent.worker_id else branch_id
        with closing(open_sqlite_connection(self.db_path)) as connection:
            governor = HostResourceGovernor(connection)
            probe = HostResourceProbe(relevant_paths=[self.db_path])
            deadline = time.monotonic() + timeout_seconds
            owned, lease = True, None
            while lease is None:
                parent_lease = self._verified_parent_lease(connection)
                if (
                    parent_lease
                    and parent_lease.workload_class == "capture_session"
                    and not self._parent_covers(parent_lease, workload_class)
                ):
                    raise BranchAdmissionDeferred("resource_parent_subbudget_exceeded")
                snapshot = (
                    self.snapshot_source() if self.snapshot_source else probe.sample(cpu_interval_seconds=0)
                )
                age = (
                    datetime.now(UTC) - datetime.fromisoformat(snapshot.sampled_at.replace("Z", "+00:00"))
                ).total_seconds()
                if not 0 <= age <= 30:
                    raise BranchAdmissionDeferred("resource_snapshot_stale")
                if (
                    parent_lease
                    and self._parent_covers(parent_lease, workload_class)
                    and self.borrowed.acquire(blocking=False)
                ):
                    try:
                        parent_lease = self._verified_parent_lease(connection)
                        if not self._parent_covers(parent_lease, workload_class):
                            raise BranchAdmissionDeferred("resource_parent_limits_changed")
                        # Recheck the whole parent budget, excluding its existing lease
                        # exactly once; a child must not hide other reserved capacity.
                        decision = governor.preview(
                            ResourceAdmissionRequest(
                                execution_id=parent_lease.execution_id,
                                owner_id=parent_lease.owner_id,
                                workload_class=parent_lease.workload_class,
                            ),
                            snapshot=snapshot,
                        )
                        if decision.status == "admitted":
                            lease, owned = parent_lease, False
                            break
                    finally:
                        if owned:
                            self.borrowed.release()
                else:
                    decision = governor.admit(
                        ResourceAdmissionRequest(
                            execution_id=branch_id,
                            owner_id=owner_id,
                            workload_class=workload_class,
                            parent_execution_id=self.parent.execution_id if self.parent else None,
                            lease_seconds=60,
                        ),
                        snapshot=snapshot,
                    )
                    lease = decision.lease
                if lease is None:
                    if time.monotonic() >= deadline:
                        raise BranchAdmissionDeferred(
                            decision.reason_code if timeout_seconds == 0 else "resource_wait_timeout"
                        )
                    time.sleep(0.1)
            context = (
                replace(self.parent, connection=connection, resource_lease_id=lease.id)
                if self.parent
                else ProcessExecutionContext(
                    db_path=self.db_path,
                    execution_id=branch_id,
                    connection=connection,
                    resource_lease_id=lease.id,
                )
            )
            stop = threading.Event()
            renewal_error = []

            def renew():
                with closing(open_sqlite_connection(self.db_path)) as heartbeat_connection:
                    while not stop.wait(5):
                        try:
                            renewed = HostResourceGovernor(heartbeat_connection).heartbeat(
                                lease.id, owner_id=owner_id, lease_seconds=60
                            )
                            if renewed is None:
                                raise RuntimeError("resource_lease_lost")
                        except Exception as error:
                            renewal_error.append(error)
                            return

            thread = (
                threading.Thread(target=renew, name="aido-branch-lease-heartbeat", daemon=True)
                if owned
                else None
            )
            try:
                if thread:
                    thread.start()
                with execution_scope(context):
                    yield lease
                if renewal_error:
                    raise RuntimeError("resource_lease_renewal_failed") from renewal_error[0]
            finally:
                stop.set()
                if thread:
                    thread.join(timeout=2)
                if owned:
                    ResourceRepository(connection).finish_remote_branch(lease.id, owner_id=owner_id)
                else:
                    self.borrowed.release()
