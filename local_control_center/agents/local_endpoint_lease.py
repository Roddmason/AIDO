"""Lease durable de concurrencia por endpoint local: limita las llamadas simultáneas a una cuenta entre procesos.

Cada ejecución corre en su propio proceso runner, así que un semáforo en memoria no limita nada: los slots viven en
SQLite (`local_endpoint_leases`), se toman con `BEGIN IMMEDIATE`, vencen por TTL (un runner muerto no bloquea el
endpoint para siempre) y llevan un fence monótono por slot para que un holder vencido no libere el slot que ya
tomó otro. Ninguna espera mantiene abierta una transacción y el reloj es de pared porque lo comparan procesos
distintos. Raises: `LocalRuntimeError("local_endpoint_busy")` si no se libera un slot dentro de la espera acotada.

@author Rodrigo Mason
"""

from __future__ import annotations

import math
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass

from local_control_center.process_supervision.context import (
    assert_external_boundary,
    remaining_execution_timeout,
)
from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.settings.repository import UNSET, SettingsRepository

from .local_runtime_causes import LocalRuntimeError

LOCAL_ENDPOINT_WAIT_SECONDS = 120.0
LOCAL_ENDPOINT_POLL_SECONDS = 0.1
LOCAL_LEASE_TTL_MARGIN_SECONDS = 60.0
MAX_CALL_SECONDS_KEY = "runtime.local.maxCallSeconds"


@dataclass(frozen=True)
class LocalEndpointLeaseToken:
    """Slot tomado: identifica al holder y el fence con el que debe liberarlo."""

    provider_id: str
    slot: int
    holder: str
    fence: int


def max_local_call_seconds(connection: sqlite3.Connection) -> int:
    """Tope global de una llamada a un modelo local (`runtime.local.maxCallSeconds`), validado por el registry."""
    descriptor = descriptor_for(MAX_CALL_SECONDS_KEY)
    if descriptor is None:
        raise KeyError(f"Runtime setting is not registered: {MAX_CALL_SECONDS_KEY}")
    stored = SettingsRepository(connection).get_value(MAX_CALL_SECONDS_KEY, "general", None)
    try:
        return int(validate_value(descriptor, descriptor.default if stored is UNSET else stored))
    except ValueError:
        return int(descriptor.default)


def local_endpoint_wait_seconds() -> float:
    """Espera máxima por un slot en esta llamada: nunca más que lo que le queda a la ejecución en curso.

    Se evalúa al tomar el slot (no al resolver el provider), así una llamada con el deadline casi agotado
    no espera los 120 s completos.

    Raises:
        ExecutionDeadlineExceeded: propagado de ``remaining_execution_timeout`` si la ejecución ya no
            tiene presupuesto.
    """
    if LOCAL_ENDPOINT_WAIT_SECONDS <= 0:
        return 0.0
    budget = remaining_execution_timeout(math.ceil(LOCAL_ENDPOINT_WAIT_SECONDS))
    return float(min(LOCAL_ENDPOINT_WAIT_SECONDS, budget))


def _try_acquire(
    connection: sqlite3.Connection,
    provider_id: str,
    *,
    limit: int,
    ttl_seconds: float,
    holder: str,
    now: float,
) -> LocalEndpointLeaseToken | None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = {
            int(row["slot"]): row
            for row in connection.execute(
                "SELECT slot, holder, fence, expires_at FROM local_endpoint_leases "
                "WHERE provider_id = ? AND slot < ?",
                (provider_id, limit),
            )
        }
        token = None
        for slot in range(limit):
            row = rows.get(slot)
            if row is None:
                connection.execute(
                    "INSERT INTO local_endpoint_leases (provider_id, slot, holder, fence, expires_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (provider_id, slot, holder, now + ttl_seconds),
                )
                token = LocalEndpointLeaseToken(provider_id, slot, holder, 1)
                break
            if row["holder"] is None or float(row["expires_at"] or 0) <= now:
                fence = int(row["fence"]) + 1
                connection.execute(
                    "UPDATE local_endpoint_leases SET holder = ?, fence = ?, expires_at = ? "
                    "WHERE provider_id = ? AND slot = ?",
                    (holder, fence, now + ttl_seconds, provider_id, slot),
                )
                token = LocalEndpointLeaseToken(provider_id, slot, holder, fence)
                break
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return token


def _release(connection: sqlite3.Connection, token: LocalEndpointLeaseToken) -> None:
    connection.execute(
        "UPDATE local_endpoint_leases SET holder = NULL, expires_at = NULL "
        "WHERE provider_id = ? AND slot = ? AND holder = ? AND fence = ?",
        (token.provider_id, token.slot, token.holder, token.fence),
    )


@contextmanager
def local_endpoint_slot(
    connection_factory: Callable[[], sqlite3.Connection],
    provider_id: str,
    *,
    limit: int,
    ttl_seconds: float,
    wait_seconds: float,
    holder: str | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[LocalEndpointLeaseToken]:
    """Toma un slot durable del endpoint local durante el bloque y lo libera al salir, también ante errores.

    Raises:
        RuntimeError: si se invoca dentro de una transacción SQLite de la operación actual.
        ValueError: si `limit`, `ttl_seconds` o `wait_seconds` están fuera de rango.
        LocalRuntimeError: `local_endpoint_busy` si ningún slot se libera dentro de `wait_seconds`.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if ttl_seconds <= 0 or wait_seconds < 0:
        raise ValueError("ttl_seconds must be positive and wait_seconds non-negative")
    assert_external_boundary()
    resolved_holder = holder or f"local-call-{uuid.uuid4().hex}"
    deadline = clock() + wait_seconds
    with closing(connection_factory()) as connection:
        while True:
            token = _try_acquire(
                connection,
                provider_id,
                limit=limit,
                ttl_seconds=ttl_seconds,
                holder=resolved_holder,
                now=clock(),
            )
            if token is not None:
                break
            if clock() >= deadline:
                raise LocalRuntimeError(
                    "local_endpoint_busy",
                    f"All {limit} slot(s) of local endpoint {provider_id} stayed busy for {wait_seconds:g}s.",
                )
            sleep(LOCAL_ENDPOINT_POLL_SECONDS)
        try:
            yield token
        finally:
            _release(connection, token)
