"""Refresco automático de la evidencia de salud de los runtimes configurados.

``runtime_readiness`` sólo acepta evidencia de salud de menos de ``HEALTH_EVIDENCE_TTL_SECONDS``,
pero el único productor de esa evidencia es una operación encolada que ejecuta el worker. Sin un
refresco automático el contrato es insatisfacible: pasados esos segundos, y después de cada
reinicio del control plane, todos los runtimes se reportan ``health_check_required`` y los perfiles
de agente quedan bloqueados aunque la configuración siga intacta.

Este módulo sólo **encola**: no sondea procesos, no llama a proveedores y no escribe evidencia. El
trabajo real lo sigue haciendo el worker con la admisión de recursos de siempre, y la operación
encolada es exactamente la misma que dispara el operador desde la UI.

Se refresca con margen: la evidencia se renueva antes de vencer, no después, para que no exista una
ventana en la que un sistema sano se reporte bloqueado.

@author Rodrigo Mason
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .runtime_readiness import HEALTH_EVIDENCE_TTL_SECONDS

REFRESH_MARGIN_SECONDS = 60
CLI_HEALTH_OPERATION = "models.health_cli_runtime"
PROVIDER_HEALTH_OPERATION = "models.provider_health_check"
CLI_ARGUMENT = "runtime_id"
PROVIDER_ARGUMENT = "provider_id"
SKIPPED_KINDS = frozenset({"manual"})
REFRESH_INTERVAL_SECONDS = max(30, HEALTH_EVIDENCE_TTL_SECONDS - REFRESH_MARGIN_SECONDS)
"""Cadencia del refresco: siempre menor que el TTL, para que nunca exista una ventana vencida."""

logger = logging.getLogger(__name__)


def _evidence_age_seconds(status: dict[str, Any], now: datetime) -> float | None:
    """Edad de la marca de salud en segundos, o ``None`` si no hay marca legible."""
    checked_at = str(status.get("healthCheckedAt") or "")
    if not checked_at:
        return None
    try:
        checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return (now - checked).total_seconds()


def needs_refresh(status: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Indica si la evidencia de ese runtime no alcanza para cubrir el próximo ciclo.

    Una marca ausente, ilegible, futura o próxima a vencer se refresca: ninguna de ellas prueba
    salud actual, y confiar en una marca dudosa es peor que gastar una ejecución barata.
    """
    age = _evidence_age_seconds(status, now or datetime.now(UTC))
    if age is None or age < 0:
        return True
    return age >= max(0, HEALTH_EVIDENCE_TTL_SECONDS - REFRESH_MARGIN_SECONDS)


def stale_health_targets(
    statuses: list[dict[str, Any]], *, now: datetime | None = None
) -> list[tuple[str, str, str]]:
    """Devuelve ``(operación, nombre del argumento, runtime)`` por runtime a refrescar.

    Sólo entra lo que está configurado: refrescar un proveedor sin credencial gastaría una
    ejecución y una llamada de red que se sabe de antemano que va a fallar. ``manual`` queda fuera
    porque no es un runtime automatizado y readiness ya lo trata aparte.
    """
    moment = now or datetime.now(UTC)
    targets: list[tuple[str, str, str]] = []
    for status in statuses:
        kind = str(status.get("kind") or "")
        if kind in SKIPPED_KINDS or not status.get("configured"):
            continue
        if not needs_refresh(status, now=moment):
            continue
        if kind == "cli":
            targets.append((CLI_HEALTH_OPERATION, CLI_ARGUMENT, str(status["id"])))
        else:
            targets.append((PROVIDER_HEALTH_OPERATION, PROVIDER_ARGUMENT, str(status["id"])))
    return targets


def _current_statuses(platform: Any) -> list[dict[str, Any]]:
    """Lee los estados por la misma vía que la UI, sin sondear procesos ni habilitar probes."""
    from .runtime_status import RuntimeStatusService

    return RuntimeStatusService(platform.connection).list_provider_statuses()


def enqueue_stale_health_checks(
    platform: Any,
    *,
    statuses: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Encola un health-check por runtime configurado sin evidencia vigente.

    Nunca propaga una excepción: se invoca desde el arranque del control plane y desde su ciclo de
    refresco, y ninguno de los dos puede caerse porque un runtime no acepte health-check.
    """
    from local_control_center.executions.router import enqueue_registered_operation

    try:
        resolved = _current_statuses(platform) if statuses is None else statuses
    except Exception as error:  # pragma: no cover - el refresco nunca impide el arranque
        logger.warning("Runtime health refresh could not read provider statuses: %s", error)
        return {"considered": 0, "enqueued": 0, "failed": 0, "runtimes": []}

    targets = stale_health_targets(resolved, now=now)
    handlers = getattr(platform, "execution_handlers", {}) or {}
    enqueued: list[str] = []
    failed = 0
    for operation, argument, runtime_id in targets:
        registered = handlers.get(operation)
        if registered is None:
            failed += 1
            logger.warning("Runtime health refresh skipped unregistered operation %s.", operation)
            continue
        try:
            enqueue_registered_operation(platform, registered[0], {argument: runtime_id})
            enqueued.append(runtime_id)
        except Exception as error:  # pragma: no cover - un runtime no puede bloquear a los demás
            failed += 1
            logger.warning("Runtime health refresh failed to enqueue %s: %s", runtime_id, error)
    if enqueued:
        logger.info("Queued %d runtime health checks: %s", len(enqueued), ", ".join(enqueued))
    return {
        "considered": len(resolved),
        "enqueued": len(enqueued),
        "failed": failed,
        "runtimes": enqueued,
    }


def refresh_from_worker(db_path: Any, cwd: Any) -> dict[str, Any]:
    """Refresca la evidencia desde el proceso worker, que es el de mantenimiento del sistema.

    Se hace acá y no en ``create_app`` a propósito: el proceso de la API no debe ser productor de
    trabajo de mantenimiento, y encolar desde su arranque altera el orden FIFO de una cola sensible
    a fencing y leases. El worker ya es dueño de ese ciclo.

    Construye su propia plataforma efímera porque el worker no monta routers y, por lo tanto, no
    tiene los handlers de operación registrados. Nunca propaga una excepción: un fallo de refresco
    no puede tumbar el worker.
    """
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.executions.registration import register_operation

    try:
        platform = ControlCenterRuntime(cwd=cwd, db_path=db_path)
    except Exception as error:  # pragma: no cover - el worker sobrevive a un refresco fallido
        logger.warning("Runtime health refresh could not open its platform: %s", error)
        return {"considered": 0, "enqueued": 0, "failed": 0, "runtimes": []}
    try:
        for operation in (CLI_HEALTH_OPERATION, PROVIDER_HEALTH_OPERATION):
            register_operation(platform, operation)
        return enqueue_stale_health_checks(platform)
    except Exception as error:  # pragma: no cover - idem
        logger.warning("Runtime health refresh failed inside the worker: %s", error)
        return {"considered": 0, "enqueued": 0, "failed": 0, "runtimes": []}
    finally:
        platform.close()
