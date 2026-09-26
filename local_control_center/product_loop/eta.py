"""Estimación de tiempo restante (ETA) de un hilo, a partir de la bitácora real de transiciones.

Tres piezas, todas pensadas para el sondeo de alta frecuencia de la consola del hilo (cada poll de
``GET /threads/{id}/events`` bajo el lock global del control-plane):

- ``state_duration_stats`` calcula mediana y p90 de permanencia por estado del FSM sobre
  ``product_loop_transitions`` (filas pequeñas); cacheada con TTL (``cached_state_duration_stats``).
- La resolución del loop del hilo (``ProductLoopRepository.latest_loop_state_for_thread``) lee sólo
  columnas *livianas* de ``product_loops`` — nunca ``context``, medido hasta ~29 MB por loop en
  producción, cuya lectura en cada sondeo degradaba todo el panel (~355 ms por hilo, frío y caliente).
- Cuando el estimador sí necesita algo del ``context`` (historias restantes, carril rápido),
  ``_cached_durable_run_fields`` lo lee y parsea una única vez por ``(loopId, versión)`` y sólo retiene
  los dos campos livianos que usa, no el contexto completo.

``estimate_thread_eta`` proyecta el camino restante en el orden normal del producto
(``CANONICAL_PATH``): un estado de espera del operador (incluye ``brief_ready``, que no reanuda solo) da
``waiting_operator`` sin número; un loop terminal no tiene ETA; si algún estado del camino tiene menos de
tres muestras históricas, ``insufficient_history`` en vez de un número inventado.

@author Rodrigo Mason
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

from local_control_center.shared.time import iso_after_seconds, utc_now

from .coordinator import _OPERATOR_WAIT_STATES, TERMINAL_STATES
from .repository import ProductLoopRepository

logger = logging.getLogger(__name__)

# Orden normal de los estados de trabajo del FSM: excluye bifurcaciones de espera del operador
# (awaiting_user, brief_ready, awaiting_approval, awaiting_feedback) y ramas opcionales
# (iteration_planning), que nunca deben sumar tiempo "de trabajo" al estimado. Fuente: el flujo
# verificado contra product_loop_transitions (126 loops / 833 transiciones) y coordinator.ALLOWED_TRANSITIONS.
CANONICAL_PATH: tuple[str, ...] = (
    "goal_received",
    "workspace_check",
    "git_check",
    "runtime_check",
    "discovery",
    "planning",
    "architecture_review",
    "backlog_ready",
    "branch_ready",
    "executing",
    "qa_running",
    "security_running",
    "quality_review",
    "review_ready",
    "delivered",
)

# Estados sin historial propio (no aparecen en el orden normal) que se resuelven a su equivalente en
# CANONICAL_PATH para no caer en "insufficient_history" por diseño en vez de por falta real de datos:
# discovering es el alias legado de discovery, reworking siempre vuelve a executing
# (phases/story_loop.py: la arista qa_running/reworking -> executing con trigger "next_story"), e
# iteration_planning es la bifurcación opcional entre backlog_ready y branch_ready
# (ALLOWED_TRANSITIONS["backlog_ready"]).
_STATE_ALIASES: dict[str, str] = {
    "discovering": "discovery",
    "reworking": "executing",
    "iteration_planning": "backlog_ready",
}

# brief_ready no integra _OPERATOR_WAIT_STATES del coordinator (esa fuente gobierna la reanudación
# durable tras un reinicio), pero para el ETA es una espera real del operador: sólo la aprobación
# explícita del brief la libera hacia backlog_ready (phases/discovery.py:781-824, api.py:355-361).
_ETA_OPERATOR_WAIT_STATES: frozenset[str] = frozenset({*_OPERATOR_WAIT_STATES, "brief_ready"})

# Estados cuya duración depende de cuántas historias quedan por procesar.
_PER_STORY_STATES: frozenset[str] = frozenset({"executing", "qa_running"})
_FAST_LANE_SKIPPED_STATES: frozenset[str] = frozenset({"architecture_review", "quality_review"})
_MIN_SAMPLES_REQUIRED = 3

_STATE_DURATION_CACHE_TTL_SECONDS = 300.0
_state_duration_cache: dict[tuple[str, int, int], tuple[float, dict[str, dict[str, Any]]]] = {}
_state_duration_cache_lock = threading.Lock()


def reset_state_duration_cache() -> None:
    """Vacía el caché TTL de permanencia por estado; usar entre tests para evitar fugas de estado global."""
    with _state_duration_cache_lock:
        _state_duration_cache.clear()


def _database_identity(connection: sqlite3.Connection) -> str:
    """Archivo de la base principal: separa las cachés de distintas bases del mismo proceso."""
    row = connection.execute("PRAGMA database_list").fetchone()
    return str(row[2] or f"memory-{id(connection)}")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _percentile(values: list[float], percentile: float) -> float:
    """Percentil por interpolación lineal sobre una lista no vacía (método estándar tipo numpy)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = percentile / 100 * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def state_duration_stats(
    connection: sqlite3.Connection,
    *,
    now_iso: str,
    window_days: int = 30,
    per_state_limit: int = 200,
) -> dict[str, dict[str, Any]]:
    """Mediana y p90 (segundos) de permanencia por estado, sobre muestras recientes.

    Toma hasta ``per_state_limit`` muestras de los últimos ``window_days`` días. Excluye esperas del
    operador y terminales: el ETA nunca necesita resolver su duración desde esta tabla. Devuelve
    ``{estado: {"median": float, "p90": float, "count": int}}``.
    """
    window_start_iso = iso_after_seconds(now_iso, -window_days * 86400)
    rows = connection.execute(
        """
        SELECT loop_id, to_state, created_at
        FROM product_loop_transitions
        WHERE created_at >= ?
        ORDER BY loop_id ASC, version ASC
        """,
        (window_start_iso,),
    ).fetchall()

    excluded_states = _ETA_OPERATOR_WAIT_STATES | TERMINAL_STATES
    samples_by_state: dict[str, list[tuple[float, float]]] = {}
    previous_row: sqlite3.Row | None = None
    for row in rows:
        if previous_row is not None and previous_row["loop_id"] == row["loop_id"]:
            state = previous_row["to_state"]
            if state not in excluded_states:
                start = _parse_iso(previous_row["created_at"])
                end = _parse_iso(row["created_at"])
                duration = (end - start).total_seconds()
                if duration >= 0:
                    samples_by_state.setdefault(state, []).append((end.timestamp(), duration))
        previous_row = row

    stats: dict[str, dict[str, Any]] = {}
    for state, samples in samples_by_state.items():
        samples.sort(key=lambda item: item[0], reverse=True)
        durations = [duration for _, duration in samples[:per_state_limit]]
        stats[state] = {
            "median": _percentile(durations, 50),
            "p90": _percentile(durations, 90),
            "count": len(durations),
        }
    return stats


def cached_state_duration_stats(
    connection: sqlite3.Connection,
    *,
    now_iso: str,
    window_days: int = 30,
    per_state_limit: int = 200,
) -> dict[str, dict[str, Any]]:
    """``state_duration_stats`` cacheado 300 s por archivo de base de datos.

    El control-plane serializa todas las rutas HTTP bajo un lock global (``control_plane.overview``);
    sin este caché, cada sondeo de 1 s de la consola del hilo recalcularía la bitácora completa de
    transiciones bajo ese lock.
    """
    key = (_database_identity(connection), window_days, per_state_limit)
    now = time.monotonic()
    with _state_duration_cache_lock:
        cached = _state_duration_cache.get(key)
        if cached is not None and now - cached[0] < _STATE_DURATION_CACHE_TTL_SECONDS:
            return cached[1]
    stats = state_duration_stats(
        connection, now_iso=now_iso, window_days=window_days, per_state_limit=per_state_limit
    )
    with _state_duration_cache_lock:
        _state_duration_cache[key] = (time.monotonic(), stats)
    return stats


def _remaining_story_count(durable_run: dict[str, Any]) -> int:
    """Historias aún no ``done`` en el cursor durable; mínimo 1 (aún no planificadas o sin cursor)."""
    entries = durable_run.get("storyProgress") or []
    pending = sum(
        1 for entry in entries if isinstance(entry, dict) and str(entry.get("status") or "") != "done"
    )
    return max(pending, 1)


# Caché LRU (no TTL: la clave incluye la versión del loop, así que una entrada nunca queda obsoleta
# mientras el loop no avance) de los únicos dos campos del ``context`` que el estimador necesita.
# Acotado por capacidad, no por tiempo, porque un proceso de larga duración vería crecer las
# combinaciones (loopId, versión) sin límite.
_DURABLE_RUN_CACHE_CAPACITY = 2048
_durable_run_cache: OrderedDict[tuple[str, str, int], dict[str, Any]] = OrderedDict()
_durable_run_cache_lock = threading.Lock()


def reset_durable_run_cache() -> None:
    """Vacía el caché de campos del ``context``; usar entre tests para evitar fugas de estado global."""
    with _durable_run_cache_lock:
        _durable_run_cache.clear()


def _cached_durable_run_fields(
    connection: sqlite3.Connection, repository: ProductLoopRepository, *, loop_id: str, version: int
) -> dict[str, Any]:
    """Lee y parsea ``context`` una sola vez por ``(loopId, versión)``; retiene sólo campos livianos.

    ``context`` puede pesar hasta ~29 MB por loop en producción; leerlo y parsearlo en cada sondeo del
    hilo (bajo el lock global del control-plane) es la causa medida de la degradación (~355 ms por
    hilo). La versión del loop sólo cambia con una transición nueva, así que el caché es válido durante
    toda la permanencia en un estado.
    """
    key = (_database_identity(connection), loop_id, version)
    with _durable_run_cache_lock:
        cached = _durable_run_cache.get(key)
        if cached is not None:
            _durable_run_cache.move_to_end(key)
            return cached
    loop = repository.get_loop(loop_id)
    durable_run = dict((loop.get("context") or {}).get("durableRun") or {})
    fields = {
        "remainingStories": _remaining_story_count(durable_run),
        "fastLane": str((durable_run.get("specDriven") or {}).get("plan") or "") == "skipped_low_risk",
    }
    with _durable_run_cache_lock:
        _durable_run_cache[key] = fields
        _durable_run_cache.move_to_end(key)
        while len(_durable_run_cache) > _DURABLE_RUN_CACHE_CAPACITY:
            _durable_run_cache.popitem(last=False)
    return fields


def _waiting_operator_eta(state: str) -> dict[str, Any]:
    return {
        "status": "waiting_operator",
        "remainingSeconds": None,
        "remainingP90Seconds": None,
        "currentState": state,
        "sampleCount": 0,
        "includesOperatorApproval": False,
    }


def _insufficient_history_eta(
    state: str, *, sample_count: int, includes_operator_approval: bool
) -> dict[str, Any]:
    return {
        "status": "insufficient_history",
        "remainingSeconds": None,
        "remainingP90Seconds": None,
        "currentState": state,
        "sampleCount": sample_count,
        "includesOperatorApproval": includes_operator_approval,
    }


def estimate_thread_eta(
    connection: sqlite3.Connection,
    *,
    thread_id: str,
    project_id: str,
    now_iso: str | None = None,
) -> dict[str, Any] | None:
    """Estima el tiempo restante del hilo desde su loop vigente y la bitácora de transiciones.

    Devuelve ``None`` cuando el hilo aún no tiene loop (nada que estimar todavía), el loop resuelto no
    pertenece a ``project_id`` o el loop ya es terminal (nada restante). El resto de los casos siempre
    trae ``status``; nunca lanza para un dato faltante o insuficiente, sólo para una conexión inválida
    (el caller decide si eso rompe el request). Sólo lee ``context`` (el campo caro) cuando el estado
    actual exige seguir calculando; los cierres tempranos (terminal, espera del operador, estado sin
    historial posible) nunca lo tocan.
    """
    resolved_now = now_iso or utc_now()
    repository = ProductLoopRepository(connection)
    summary = repository.latest_loop_state_for_thread(thread_id)
    if summary is None or summary["projectId"] != project_id:
        return None
    state = str(summary["state"])
    if state in TERMINAL_STATES:
        return None
    if state in _ETA_OPERATOR_WAIT_STATES:
        return _waiting_operator_eta(state)

    resolved_state = _STATE_ALIASES.get(state, state)
    review_ready_index = CANONICAL_PATH.index("review_ready")
    if resolved_state not in CANONICAL_PATH:
        return _insufficient_history_eta(state, sample_count=0, includes_operator_approval=False)

    index = CANONICAL_PATH.index(resolved_state)
    includes_operator_approval = index <= review_ready_index

    last_transition = repository.latest_transition_for_loop(summary["id"])
    version = int(last_transition["version"]) if last_transition else 0
    # Sin ninguna transición todavía (loop recién creado, previo a su primer avance real) no hay una
    # columna liviana que traiga su fecha de creación: se asume elapsed=0 en ese instante brevísimo en
    # vez de pagar el costo de leer ``created_at`` (posterior a ``context`` en el esquema físico).
    entered_at = last_transition["createdAt"] if last_transition else resolved_now
    elapsed_seconds = max((_parse_iso(resolved_now) - _parse_iso(entered_at)).total_seconds(), 0.0)

    durable_fields = _cached_durable_run_fields(
        connection, repository, loop_id=summary["id"], version=version
    )
    fast_lane = bool(durable_fields["fastLane"])
    remaining_stories = int(durable_fields["remainingStories"])
    remaining_path = [
        candidate
        for candidate in CANONICAL_PATH[index + 1 :]
        if candidate != "delivered" and not (fast_lane and candidate in _FAST_LANE_SKIPPED_STATES)
    ]
    # El estado actual, si es executing o qa_running, ya "gastó" su propio turno en CANONICAL_PATH:
    # las historias adicionales (más allá de la que está en curso) necesitan su propio ciclo completo
    # de executing+qa_running, que remaining_path ya no puede aportar (ninguno de los dos vuelve a
    # aparecer después del índice actual). Sin este ajuste, 3 historias restantes con el loop en
    # executing subestiman el resto: sólo se contaría un qa_running extra, nunca el executing de las
    # historias 2 y 3.
    extra_story_cycles = max(remaining_stories - 1, 0) if resolved_state in _PER_STORY_STATES else 0

    required_states = {resolved_state, *remaining_path}
    if extra_story_cycles:
        required_states |= _PER_STORY_STATES

    stats = cached_state_duration_stats(connection, now_iso=resolved_now)
    sample_count = min((stats.get(candidate, {}).get("count", 0) for candidate in required_states), default=0)
    if sample_count < _MIN_SAMPLES_REQUIRED:
        return _insufficient_history_eta(
            state, sample_count=sample_count, includes_operator_approval=includes_operator_approval
        )

    current_stats = stats[resolved_state]
    remaining_median = max(current_stats["median"] - elapsed_seconds, 0.0)
    remaining_p90 = max(current_stats["p90"] - elapsed_seconds, 0.0)
    for candidate in remaining_path:
        weight = remaining_stories if candidate in _PER_STORY_STATES else 1
        remaining_median += stats[candidate]["median"] * weight
        remaining_p90 += stats[candidate]["p90"] * weight
    if extra_story_cycles:
        cycle_median = stats["executing"]["median"] + stats["qa_running"]["median"]
        cycle_p90 = stats["executing"]["p90"] + stats["qa_running"]["p90"]
        remaining_median += cycle_median * extra_story_cycles
        remaining_p90 += cycle_p90 * extra_story_cycles

    return {
        "status": "estimating",
        "remainingSeconds": round(remaining_median),
        "remainingP90Seconds": round(remaining_p90),
        "currentState": state,
        "sampleCount": sample_count,
        "includesOperatorApproval": includes_operator_approval,
    }


def safe_estimate_thread_eta(
    connection: sqlite3.Connection,
    *,
    thread_id: str,
    project_id: str,
    now_iso: str | None = None,
) -> dict[str, Any] | None:
    """Envoltorio best-effort de ``estimate_thread_eta``: un fallo nunca rompe el endpoint del hilo."""
    try:
        return estimate_thread_eta(connection, thread_id=thread_id, project_id=project_id, now_iso=now_iso)
    except Exception:
        logger.warning("Thread ETA estimation failed for thread %s.", thread_id, exc_info=True)
        return None
