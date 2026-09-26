"""RSS actual por lease de recursos, agregando el árbol nativo de cada proceso administrado.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

import psutil

_IDENTITY_TOLERANCE_SECONDS = 0.01


def live_memory_by_lease(connection: sqlite3.Connection) -> dict[str, int]:
    """Suma el RSS en vivo del árbol de cada proceso administrado, agrupado por su lease.

    Cada fila de ``managed_processes`` sin ``finished_at`` es un árbol completo (raíz más
    descendientes nativos), no un único PID; varias filas comparten la misma lease cuando una
    rama pidió prestado el presupuesto de su padre (``host_resources/branch_admission.py``), y sus
    contribuciones se suman. El desalojo graduado (``HostResourceGovernor.violations_for_snapshot``)
    usa este uso real, nunca ``peak_memory_bytes`` (pico histórico que nunca baja).

    Una identidad no verificable (PID reutilizado, proceso ya terminado, acceso denegado) nunca
    propaga: ese árbol aporta 0 al total de su lease, pero la lease sigue apareciendo en el
    resultado porque la fila en ``managed_processes`` sigue viva. Sólo una lease sin ninguna fila
    viva (ni proceso ni contenedor) queda ausente del diccionario.

    Una carga respaldada por un contenedor Docker (``managed_containers``, ``docker.py``) también
    cuenta como viva, pero su RSS no se mide por este camino: aporta 0 (ver ADR-005). Sin esto, una
    lease sólo respaldada por un contenedor nunca sería candidata al desalojo graduado.
    """
    usage: dict[str, int] = {}
    rows = connection.execute(
        """
        SELECT resource_lease_id, root_pid, root_create_time
        FROM managed_processes
        WHERE finished_at IS NULL AND resource_lease_id IS NOT NULL
        """
    ).fetchall()
    for lease_id, root_pid, root_create_time in rows:
        usage[lease_id] = usage.get(lease_id, 0) + _tree_rss_bytes(root_pid, root_create_time)
    for (lease_id,) in connection.execute(
        "SELECT resource_lease_id FROM managed_containers WHERE released_at IS NULL AND resource_lease_id IS NOT NULL"
    ).fetchall():
        usage.setdefault(lease_id, 0)
    return usage


def _tree_rss_bytes(root_pid: int, root_create_time: float) -> int:
    """RSS actual de un árbol nativo; degrada a 0 ante cualquier identidad o acceso no verificable."""
    if not root_pid or not root_create_time:
        return 0  # Sin PID o sin hora de creación registrada no hay identidad que verificar.
    try:
        root = psutil.Process(root_pid)
        if abs(root.create_time() - root_create_time) >= _IDENTITY_TOLERANCE_SECONDS:
            return 0  # PID reutilizado: no es el árbol que managed_processes registró.
        members = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return 0
    total = 0
    for member in members:
        try:
            total += member.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return total
