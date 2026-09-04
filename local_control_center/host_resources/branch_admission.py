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
from .repository import ResourceRepository

_BORROW_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_BORROW_REGISTRY_LOCK = threading.Lock()


class BranchAdmission:
    """Comparte sólo una reserva padre por vez y respeta los límites de otras ejecuciones."""

    def __init__(self, db_path: Path, *, snapshot_source=None):
        self.db_path = db_path
        self.parent = CURRENT_EXECUTION.get()
        key = (str(db_path.resolve()), self.parent.resource_lease_id if self.parent else None)
        with _BORROW_REGISTRY_LOCK:
            self.borrowed = _BORROW_LOCKS.setdefault(key, threading.Lock())
        self.snapshot_source = snapshot_source

    @contextmanager
    def reserve(self, branch_id: str, workload_class: str):
        """Espera capacidad con polling cancelable; renueva y libera únicamente las leases propias."""
        owner_id = self.parent.worker_id if self.parent and self.parent.worker_id else branch_id
        with closing(open_sqlite_connection(self.db_path)) as connection:
            governor = HostResourceGovernor(connection)
            probe = HostResourceProbe(relevant_paths=[self.db_path])
            deadline = time.monotonic() + 900
            owned, lease = True, None
            while lease is None:
                parent_lease = (
                    ResourceRepository(connection).active_lease_for_execution(self.parent.execution_id)
                    if self.parent and self.parent.execution_id
                    else None
                )
                if self.parent and self.parent.fencing_token is not None:
                    from local_control_center.executions.repository import ExecutionRepository

                    ExecutionRepository(connection).require_fence(
                        self.parent.execution_id,
                        owner_id=self.parent.worker_id,
                        fencing_token=self.parent.fencing_token,
                    )
                if (
                    self.parent
                    and self.parent.execution_id
                    and ManagedProcessRepository(connection).cancellation_reason(self.parent.execution_id)
                ):
                    raise RuntimeError("execution_cancelled")
                if (
                    parent_lease
                    and parent_lease.workload_class == workload_class
                    and self.borrowed.acquire(blocking=False)
                ):
                    lease, owned = parent_lease, False
                    break
                snapshot = (
                    self.snapshot_source() if self.snapshot_source else probe.sample(cpu_interval_seconds=0)
                )
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
                        raise RuntimeError("resource_wait_timeout")
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
