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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .runtime_readiness import HEALTH_EVIDENCE_TTL_SECONDS

REFRESH_MARGIN_SECONDS = 120
CLI_HEALTH_OPERATION = "models.health_cli_runtime"
PROVIDER_HEALTH_OPERATION = "models.provider_health_check"
CLI_ARGUMENT = "runtime_id"
PROVIDER_ARGUMENT = "provider_id"
SKIPPED_KINDS = frozenset({"manual"})
REFRESH_STALENESS_SECONDS = max(30, HEALTH_EVIDENCE_TTL_SECONDS - REFRESH_MARGIN_SECONDS)
"""Edad a partir de la cual una evidencia se considera rancia y entra al proximo ciclo."""

REFRESH_POLL_SECONDS = 30
"""Cada cuanto MIRA el worker si hay evidencia rancia. Es distinto del umbral, a proposito.

Cuando ambos valian 240 s, un runtime cuya evidencia cruzaba el umbral justo despues de un ciclo
esperaba otros 240 s: llegaba a 480 s y vencia (TTL 300). Medido en la instalacion real con
gemini en 365 s, nvidia_nim en 357 s y omniroute en 318 s, los tres reportando
`health_check_required` con la configuracion intacta. Mirar seguido y refrescar solo lo rancio
deja presupuesto para encolar, admitir y ejecutar antes del vencimiento.
"""

BACKOFF_SCHEDULE_SECONDS = (60, 300, 900)
"""Espera tras el 1.er, 2.º y 3.er (o posterior) refresco improductivo seguido de un objetivo."""


@dataclass
class _TargetBackoff:
    """Historial de refrescos improductivos de un objetivo."""

    failures: int = 0
    awaiting_outcome: bool = False
    attempted_at: datetime | None = None
    retry_after: datetime | None = None


class HealthRefreshBackoff:
    """Cooldown exponencial por objetivo para el refresco automático de salud.

    Un refresco es improductivo cuando su ejecución ya no está pendiente y no dejó evidencia
    vigente y ``healthy``: falló, se canceló o el runtime quedó unhealthy (cada check escribe su
    marca aunque falle, ``provider_accounts.py:605-630``). Tras cada improductivo el objetivo espera
    60 s, luego 5 min y después 15 min como máximo, contados desde el intento; solo una evidencia
    vigente con ``healthStatus == "healthy"`` (el mismo criterio de ``healthy_evidence`` en
    readiness) lo reinicia. Solo gobierna el refresco automático: el health check que dispara el
    operador por la API no pasa por aquí.
    """

    def __init__(self) -> None:
        self._targets: dict[tuple[str, str], _TargetBackoff] = {}

    def reset(self, target: tuple[str, str]) -> None:
        """Olvida el historial del objetivo porque su evidencia volvió a estar vigente."""
        self._targets.pop(target, None)

    def allows(self, target: tuple[str, str], *, now: datetime) -> bool:
        """Registra el desenlace improductivo pendiente y dice si el objetivo puede encolarse ya."""
        state = self._targets.get(target)
        if state is None:
            return True
        if state.awaiting_outcome and state.attempted_at is not None:
            state.awaiting_outcome = False
            state.failures += 1
            delay = BACKOFF_SCHEDULE_SECONDS[min(state.failures, len(BACKOFF_SCHEDULE_SECONDS)) - 1]
            state.retry_after = state.attempted_at + timedelta(seconds=delay)
        return state.retry_after is None or now >= state.retry_after

    def record_enqueued(self, target: tuple[str, str], *, now: datetime) -> None:
        """Marca un refresco en vuelo cuyo desenlace se evalúa en el próximo ciclo."""
        state = self._targets.setdefault(target, _TargetBackoff())
        state.awaiting_outcome = True
        state.attempted_at = now


_BACKOFF_BY_DATABASE: dict[str, HealthRefreshBackoff] = {}


def backoff_for(database: Any) -> HealthRefreshBackoff:
    """Devuelve el cooldown del proceso para esa base; el worker vive lo suficiente para recordarlo."""
    key = str(Path(str(database)).resolve(strict=False))
    return _BACKOFF_BY_DATABASE.setdefault(key, HealthRefreshBackoff())


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
    return age >= REFRESH_STALENESS_SECONDS


def _refresh_target(status: dict[str, Any]) -> tuple[str, str, str] | None:
    """``(operación, argumento, runtime)`` de un runtime refrescable, o ``None`` si no aplica."""
    kind = str(status.get("kind") or "")
    if kind in SKIPPED_KINDS or not status.get("configured"):
        return None
    if kind == "cli":
        return (CLI_HEALTH_OPERATION, CLI_ARGUMENT, str(status["id"]))
    return (PROVIDER_HEALTH_OPERATION, PROVIDER_ARGUMENT, str(status["id"]))


def stale_health_targets(
    statuses: list[dict[str, Any]], *, now: datetime | None = None
) -> list[tuple[str, str, str]]:
    """Devuelve ``(operación, nombre del argumento, runtime)`` por runtime a refrescar.

    Sólo entra lo que está configurado: refrescar un proveedor sin credencial gastaría una
    ejecución y una llamada de red que se sabe de antemano que va a fallar. ``manual`` queda fuera
    porque no es un runtime automatizado y readiness ya lo trata aparte.
    """
    moment = now or datetime.now(UTC)
    return [
        target
        for status in statuses
        if (target := _refresh_target(status)) is not None and needs_refresh(status, now=moment)
    ]


def _pending_health_checks(platform: Any) -> tuple[int, set[tuple[str, str]], set[str]]:
    """Identifica objetivos pendientes sin publicar los inputs cifrados de la operación."""
    from local_control_center.credentials.backends import CredentialBackendError
    from local_control_center.executions.inputs import OperationInputStore
    from local_control_center.executions.models import TERMINAL_STATUSES
    from local_control_center.shared.serialization import json_loads

    placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    rows = platform.connection.execute(
        f"""
        SELECT operation, arguments_json FROM operational_executions
        WHERE operation IN (?, ?) AND status NOT IN ({placeholders})
        """,
        (CLI_HEALTH_OPERATION, PROVIDER_HEALTH_OPERATION, *sorted(TERMINAL_STATUSES)),
    ).fetchall()
    inputs = OperationInputStore(platform.db_path)
    targets: set[tuple[str, str]] = set()
    unresolved_operations: set[str] = set()
    for row in rows:
        operation = str(row["operation"])
        argument = CLI_ARGUMENT if operation == CLI_HEALTH_OPERATION else PROVIDER_ARGUMENT
        try:
            arguments = json_loads(row["arguments_json"], {})
            locator = arguments.get("sealedInput") if isinstance(arguments, dict) else None
            payload = inputs.get(locator) if isinstance(locator, str) else arguments
            target = payload.get(argument) if isinstance(payload, dict) else None
            if not isinstance(target, str) or not target.strip():
                raise CredentialBackendError("Health check target is unavailable.")
            targets.add((operation, target))
        except (CredentialBackendError, TypeError, ValueError):
            # Sin identidad no se puede probar ausencia de duplicados. El bloqueo queda
            # acotado a esta operación, sin impedir la otra clase de health-check.
            unresolved_operations.add(operation)
    return len(rows), targets, unresolved_operations


def _current_statuses(platform: Any) -> list[dict[str, Any]]:
    """Lee los estados por la misma vía que la UI, sin sondear procesos ni habilitar probes."""
    from .runtime_status import RuntimeStatusService

    return RuntimeStatusService(platform.connection).list_provider_statuses()


def enqueue_stale_health_checks(
    platform: Any,
    *,
    statuses: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    backoff: HealthRefreshBackoff | None = None,
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
        return {"considered": 0, "enqueued": 0, "failed": 0, "runtimes": [], "pending": 0}

    pending, pending_targets, unresolved_operations = _pending_health_checks(platform)
    moment = now or datetime.now(UTC)
    cooldown = backoff or backoff_for(platform.db_path)
    for status in resolved:
        fresh_target = _refresh_target(status)
        if (
            fresh_target is not None
            and status.get("healthStatus") == "healthy"
            and not needs_refresh(status, now=moment)
        ):
            cooldown.reset((fresh_target[0], fresh_target[2]))

    targets = stale_health_targets(resolved, now=moment)
    handlers = getattr(platform, "execution_handlers", {}) or {}
    enqueued: list[str] = []
    cooling: list[str] = []
    failed = 0
    for operation, argument, runtime_id in targets:
        target = (operation, runtime_id)
        if target in pending_targets or operation in unresolved_operations:
            continue
        if not cooldown.allows(target, now=moment):
            cooling.append(runtime_id)
            continue
        registered = handlers.get(operation)
        if registered is None:
            failed += 1
            logger.warning("Runtime health refresh skipped unregistered operation %s.", operation)
            continue
        try:
            enqueue_registered_operation(platform, registered[0], {argument: runtime_id})
            enqueued.append(runtime_id)
            pending_targets.add(target)
            cooldown.record_enqueued(target, now=moment)
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
        "pending": pending,
        "coolingDown": cooling,
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
        return {"considered": 0, "enqueued": 0, "failed": 0, "runtimes": [], "pending": 0}
    try:
        for operation in (CLI_HEALTH_OPERATION, PROVIDER_HEALTH_OPERATION):
            register_operation(platform, operation)
        return enqueue_stale_health_checks(platform)
    except Exception as error:  # pragma: no cover - idem
        logger.warning("Runtime health refresh failed inside the worker: %s", error)
        return {"considered": 0, "enqueued": 0, "failed": 0, "runtimes": [], "pending": 0}
    finally:
        platform.close()
