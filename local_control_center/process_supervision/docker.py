"""Supervisa el CLI Docker y conserva la reserva hasta retirar su contenedor remoto.

El contenedor pertenece al daemon, no al Job Object del cliente; su nombre aleatorio se
registra antes del run y permite cleanup acotado y recuperable. Nunca se usa prune.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest
from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.settings import default_db_path
from local_control_center.shared.time import utc_now

from .context import CURRENT_EXECUTION, ProcessExecutionContext, assert_external_boundary, execution_scope
from .repository import process_create_time
from .service import ResourceWaitError, run_supervised_capture


def run_docker_capture(
    args: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    workload_class: str = "build_heavy",
) -> dict[str, Any]:
    """Admite el contenedor y conserva su lease incluso si el cliente Docker es cancelado.

    El llamador declara el perfil porque solo el conoce el cap real del contenedor: `--memory` es
    un limite duro de cgroup, y reservar mucho mas que eso bloquea trabajo que si cabria.
    """
    assert_external_boundary()
    context = CURRENT_EXECUTION.get() or ProcessExecutionContext(db_path=default_db_path())
    name = f"aido-p0-{uuid.uuid4().hex}"
    lease_id = context.resource_lease_id
    owns_lease = lease_id is None
    snapshot = HostResourceProbe(relevant_paths=[cwd, context.db_path.parent]).sample(cpu_interval_seconds=0)
    with closing(open_sqlite_connection(context.db_path)) as connection:
        initialize_platform_schema(connection)
        if lease_id is None:
            decision = HostResourceGovernor(connection).admit(
                ResourceAdmissionRequest(
                    execution_id=context.execution_id or name,
                    workload_class=workload_class,
                    owner_id=name,
                    lease_seconds=timeout_seconds + 60,
                ),
                snapshot=snapshot,
            )
            if decision.lease is None:
                raise ResourceWaitError(f"resource_wait: {decision.reason_code}")
            lease_id = decision.lease.id
        connection.execute(
            """INSERT INTO managed_containers
            (name, execution_id, executable, owner_pid, owner_create_time, resource_lease_id,
             owns_lease, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                context.execution_id or name,
                args[0],
                os.getpid(),
                process_create_time(os.getpid()),
                lease_id,
                int(owns_lease),
                utc_now(),
            ),
        )
    command = [*args[:3], "--name", name, *args[3:]]
    try:
        with execution_scope(
            replace(context, execution_id=context.execution_id or name, resource_lease_id=lease_id)
        ):
            # La etapa supervisada declara el MISMO perfil que la lease: si no coinciden, el
            # guard de "etapa pesada requiere reserva pesada" rechaza el contenedor entero.
            return run_supervised_capture(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                workload_class=workload_class,
            )
    finally:
        cleanup_container(context.db_path, name=name, cwd=cwd)


def cleanup_container(db_path: Path, *, name: str, cwd: Path) -> None:
    """Retira sólo un nombre previamente registrado; si Docker no responde, conserva la reserva."""
    with closing(open_sqlite_connection(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM managed_containers WHERE name = ? AND released_at IS NULL", (name,)
        ).fetchone()
    if row is None:
        return
    result = run_supervised_capture(
        [row["executable"], "rm", "--force", name],
        cwd=cwd,
        timeout_seconds=10,
        workload_class="control_plane",
        cleanup_only=True,
        db_path=db_path,
    )
    absent = "no such container" in str(result["stderr"]).lower()
    if result["timedOut"] or (result["returnCode"] != 0 and not absent):
        raise RuntimeError("No se pudo verificar el cleanup del contenedor AIDO; su reserva sigue activa.")
    with closing(open_sqlite_connection(db_path)) as connection:
        connection.execute("UPDATE managed_containers SET released_at = ? WHERE name = ?", (utc_now(), name))
        if row["owns_lease"]:
            ResourceRepository(connection).release(row["resource_lease_id"], reason="container_removed")
