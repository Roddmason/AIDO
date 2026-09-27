"""Tablero por historia del product loop: orden de ejecución, estado persistido y columnas del hilo.

Módulo puro (sin base de datos ni FSM) compartido por el loop y por el endpoint del tablero: decide en
qué orden ejecuta el developer las historias de un loop (prioridad ``critical > high > medium > low``,
desconocida = ``medium``, y en empate el orden de emisión del backlog), cuándo una historia ya está
terminada, la huella estable de su spec para reconocerla en un reintento, la forma de cada entrada del
cursor durable ``durableRun.storyProgress`` y cómo se proyectan historias, tareas y cursor a las cuatro
columnas del tablero (``todo``/``in_progress``/``qa``/``done``). Diseño:
docs/superpowers/specs/2026-09-22-per-story-execution-board-design.md §3.1, §3.4 y §4.

Cada tarjeta expone además quién trabaja la historia (``assignee``: rol del loop, runtime y modelo
del cursor), su avance (``progressPercent``) y sus criterios con marca de cumplimiento. El avance es
la media, sobre las tareas de la historia, del peso de la etapa de cada tarea (``STAGE_WEIGHT``:
``todo``/``reopened`` 0, ``in_progress``/``blocked`` 1/3, ``qa`` 2/3, ``done`` 1); una historia sin
tareas usa su propio estado. El avance del tablero es la media simple de las tarjetas. Un criterio
cuenta como cumplido si su estado persistido lo dice o si la historia quedó ``done``: el loop solo
cierra una historia tras pasar su gate QA (o sin cambios que verificar).

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STORY_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
DEFAULT_PRIORITY_RANK = STORY_PRIORITY_RANK["medium"]
GENERAL_BATCH_ID = "__general__"
STORY_STATUS_TODO = "todo"
STORY_STATUS_IN_PROGRESS = "in_progress"
STORY_STATUS_QA = "qa"
STORY_STATUS_DONE = "done"
STORY_STATUS_BLOCKED = "blocked"
BOARD_COLUMNS = ("todo", "in_progress", "qa", "done")
_COLUMN_BY_STATUS = {
    STORY_STATUS_IN_PROGRESS: "in_progress",
    STORY_STATUS_QA: "qa",
    STORY_STATUS_DONE: "done",
    STORY_STATUS_BLOCKED: "in_progress",
}
STAGE_WEIGHT = {
    "todo": 0.0,
    "reopened": 0.0,
    STORY_STATUS_IN_PROGRESS: 1 / 3,
    STORY_STATUS_BLOCKED: 1 / 3,
    STORY_STATUS_QA: 2 / 3,
    STORY_STATUS_DONE: 1.0,
}
_MET_CRITERION_STATUSES = frozenset({"met", "passed", "verified", "done", "accepted"})
_ACTIVE_ROLE_BY_STATUS = {STORY_STATUS_IN_PROGRESS: "developer", STORY_STATUS_QA: "qa"}
_WORKING_LOOP_STATES = frozenset({"executing", "qa_running", "reworking"})
_STAGE_BY_LOOP_STATE = {
    "executing": "executing",
    "qa_running": "executing",
    "reworking": "executing",
    "security_running": "security",
    "quality_review": "security",
    "review_ready": "approval",
    "awaiting_approval": "approval",
    "awaiting_feedback": "approval",
    "delivered": "delivered",
    "blocked": "blocked",
    "cancelled": "blocked",
}


def priority_rank(priority: str | None) -> int:
    """Rango numérico de la prioridad de una historia; vacía o desconocida cuenta como ``medium``."""
    return STORY_PRIORITY_RANK.get(str(priority or "").strip().lower(), DEFAULT_PRIORITY_RANK)


def order_story_batches(
    agent_tasks: list[dict[str, Any]],
    stories_by_id: dict[str, dict[str, Any]],
    *,
    story_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Agrupa las tareas por historia y ordena los lotes por prioridad y orden de emisión.

    El orden de emisión es ``story_order`` (las historias del output del PO en el orden en que las
    emitió); las historias que no figuran en él van después, por primera aparición en
    ``agent_tasks`` (el Technical Lead puede devolver las tareas en cualquier orden). Toda historia de
    ``story_order`` presente en ``stories_by_id`` recibe su lote aunque no tenga tareas, para que el
    loop detecte la historia sin planificar y el tablero la muestre. Las tareas cuya historia no
    resuelve forman un lote sin historia (``GENERAL_BATCH_ID``) que siempre va al final. Cada lote es
    ``{"storyId", "story", "tasks"}`` y conserva los dicts originales de las tareas.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    general: list[dict[str, Any]] = []
    for task in agent_tasks or []:
        story_id = str(task.get("storyId") or "").strip()
        if story_id and story_id in stories_by_id:
            grouped.setdefault(story_id, []).append(task)
        else:
            general.append(task)
    backlog_order = [story_id for story_id in story_order or [] if story_id in stories_by_id]
    for story_id in backlog_order:
        grouped.setdefault(story_id, [])
    emission = {
        story_id: position for position, story_id in enumerate(dict.fromkeys([*backlog_order, *grouped]))
    }
    ordered = sorted(
        grouped,
        key=lambda story_id: (priority_rank(stories_by_id[story_id].get("priority")), emission[story_id]),
    )
    batches = [
        {"storyId": story_id, "story": stories_by_id[story_id], "tasks": grouped[story_id]}
        for story_id in ordered
    ]
    if general:
        batches.append({"storyId": GENERAL_BATCH_ID, "story": None, "tasks": general})
    return batches


def story_batch_is_done(batch: dict[str, Any]) -> bool:
    """Indica si el lote ya terminó: la historia y todas sus tareas en ``done``.

    Un ``request_changes`` que devuelve una tarea a ``todo`` o un ``reopen_story`` reabren el lote.
    """
    tasks = batch.get("tasks") or []
    tasks_done = bool(tasks) and all(str(task.get("status") or "") == STORY_STATUS_DONE for task in tasks)
    story = batch.get("story")
    if story is None:
        return tasks_done
    return tasks_done and str(story.get("status") or "") == STORY_STATUS_DONE


def story_fingerprint(story: dict[str, Any], criteria: list[str]) -> str:
    """Huella sha256 del valor de una historia: título, Como/Quiero/Para y criterios normalizados.

    Ignora mayúsculas y espacios para reconocer la misma historia cuando el PO la vuelve a emitir en
    un reintento (filas nuevas, mismo contenido); cualquier cambio de texto produce otra huella.
    """
    payload = {
        "title": _normalized(story.get("title")),
        "asA": _normalized(story.get("asA")),
        "iWant": _normalized(story.get("iWant")),
        "soThat": _normalized(story.get("soThat")),
        "criteria": [_normalized(criterion) for criterion in criteria],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def story_progress_entry(
    *,
    story_id: str,
    index: int,
    title: str,
    status: str,
    fingerprint: str = "",
    commit: str | None = None,
    qa_verdict: str | None = None,
    runs: int = 0,
    runtime: str | None = None,
    reason: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Entrada del cursor durable ``durableRun.storyProgress`` para una historia del loop.

    La forma es estable para que una ejecución futura de un job por historia (enfoque B del spec)
    la reutilice sin migración.
    """
    return {
        "storyId": story_id,
        "index": index,
        "title": title,
        "status": status,
        "fingerprint": fingerprint,
        "commit": commit,
        "qaVerdict": qa_verdict,
        "runs": runs,
        "runtime": runtime,
        "reason": reason,
        "outcome": outcome,
    }


def column_for_status(status: str | None) -> str:
    """Columna del tablero para un estado persistido; ``blocked`` se muestra en curso con su marca."""
    return _COLUMN_BY_STATUS.get(str(status or "").strip().lower(), "todo")


def stage_for_loop_state(state: str | None) -> str:
    """Etapa del tablero para el estado FSM del loop; todo lo previo a ``executing`` es ``planning``."""
    return _STAGE_BY_LOOP_STATE.get(str(state or ""), "planning")


def empty_board() -> dict[str, Any]:
    """Tablero vacío para un hilo que todavía no tiene un loop con tareas planificadas."""
    return {
        "loopId": None,
        "loopState": None,
        "stage": "planning",
        "columns": [{"id": column, "cards": []} for column in BOARD_COLUMNS],
        "progress": {"done": 0, "total": 0, "percent": 0},
    }


def build_board(
    *,
    loop_id: str,
    loop_state: str,
    agent_tasks: list[dict[str, Any]],
    stories_by_id: dict[str, dict[str, Any]],
    criteria_by_story: dict[str, list[Any]],
    progress_by_story: dict[str, dict[str, Any]],
    story_order: list[str] | None = None,
) -> dict[str, Any]:
    """Proyecta historias, tareas y cursor del loop a las cuatro columnas y el progreso del tablero.

    ``story_order`` (historias del output del PO en orden de emisión) fija el desempate y hace
    visibles las historias sin tareas planificadas. ``criteria_by_story`` admite textos o registros
    de ``acceptance_criteria`` (``{"id", "criterion", "status"}``).
    """
    columns: dict[str, list[dict[str, Any]]] = {column: [] for column in BOARD_COLUMNS}
    batches = order_story_batches(agent_tasks, stories_by_id, story_order=story_order)
    for index, batch in enumerate(batches, start=1):
        card = _board_card(
            batch,
            index=index,
            criteria=criteria_by_story.get(batch["storyId"]) or [],
            progress=progress_by_story.get(batch["storyId"]) or {},
            loop_state=loop_state,
        )
        columns[card["column"]].append(card)
    percents = [card["progressPercent"] for column in columns.values() for card in column]
    return {
        "loopId": loop_id,
        "loopState": loop_state,
        "stage": stage_for_loop_state(loop_state),
        "columns": [{"id": column, "cards": columns[column]} for column in BOARD_COLUMNS],
        "progress": {
            "done": len(columns["done"]),
            "total": len(batches),
            "percent": round(sum(percents) / len(percents)) if percents else 0,
        },
    }


def story_progress_percent(status: str, tasks: list[dict[str, Any]]) -> int:
    """Avance 0-100 de una historia: media del peso de etapa de sus tareas (o de su estado)."""
    statuses = [str(task.get("status") or "") for task in tasks] or [status]
    weights = [STAGE_WEIGHT.get(value.strip().lower(), 0.0) for value in statuses]
    return round(100 * sum(weights) / len(weights))


def _criterion(item: Any, *, story_done: bool) -> dict[str, Any]:
    record = item if isinstance(item, dict) else {"criterion": item}
    status = str(record.get("status") or "pending")
    return {
        "id": str(record.get("id") or "") or None,
        "text": str(record.get("criterion") or ""),
        "status": status,
        "met": status.strip().lower() in _MET_CRITERION_STATUSES or story_done,
    }


def _assignee(
    status: str, loop_state: str, tasks: list[dict[str, Any]], progress: dict[str, Any]
) -> dict[str, Any]:
    """Quién trabaja la historia: el rol que el loop tiene corriendo sobre ella y su runtime/modelo.

    El loop ejecuta una historia a la vez con el DeveloperAgent y luego su gate QA sobre el mismo
    runtime; ``active`` solo es verdadero mientras la historia está en esas etapas y el loop corre.
    Sin trabajo en curso, el rol es el último que la tocó (``developer`` si ya hay corridas) y
    ``plannedRoles`` lista los roles que el Technical Lead planificó en sus tareas.
    """
    role = _ACTIVE_ROLE_BY_STATUS.get(status)
    active = role is not None and loop_state in _WORKING_LOOP_STATES
    if role is None and (
        int(progress.get("runs") or 0) > 0 or status in {STORY_STATUS_DONE, STORY_STATUS_BLOCKED}
    ):
        role = "developer"
    planned = list(dict.fromkeys(str(task.get("role") or "") for task in tasks if task.get("role")))
    return {
        "role": role,
        "runtime": progress.get("runtime") or None,
        "model": progress.get("model") or None,
        "active": active,
        "plannedRoles": planned,
    }


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def _batch_status(batch: dict[str, Any]) -> str:
    story = batch.get("story")
    tasks = batch.get("tasks") or []
    if story is not None:
        story_status = str(story.get("status") or "")
        tasks_done = all(str(task.get("status") or "") == STORY_STATUS_DONE for task in tasks)
        if story_status != STORY_STATUS_DONE or tasks_done:
            return story_status
    statuses = {str(task.get("status") or "") for task in tasks}
    if statuses == {STORY_STATUS_DONE}:
        return STORY_STATUS_DONE
    for status in (STORY_STATUS_BLOCKED, STORY_STATUS_QA, STORY_STATUS_IN_PROGRESS):
        if status in statuses:
            return status
    return STORY_STATUS_TODO


def _board_card(
    batch: dict[str, Any],
    *,
    index: int,
    criteria: list[Any],
    progress: dict[str, Any],
    loop_state: str = "",
) -> dict[str, Any]:
    story = batch.get("story") or {}
    status = _batch_status(batch)
    blocked = status == STORY_STATUS_BLOCKED
    metadata = story.get("metadata") if isinstance(story.get("metadata"), dict) else {}
    tasks = batch.get("tasks") or []
    story_done = status == STORY_STATUS_DONE
    criteria_records = [_criterion(item, story_done=story_done) for item in criteria]
    return {
        "storyId": batch["storyId"],
        "index": index,
        "synthetic": batch.get("story") is None,
        "title": str(story.get("title") or ""),
        "asA": str(story.get("asA") or ""),
        "iWant": str(story.get("iWant") or ""),
        "soThat": str(story.get("soThat") or ""),
        "priority": str(story.get("priority") or "medium"),
        "status": status,
        "column": column_for_status(status),
        "blocked": blocked,
        "blockedReason": (progress.get("reason") or metadata.get("blockedReason") or None)
        if blocked
        else None,
        "outcome": metadata.get("outcome") or progress.get("outcome") or None,
        "runtime": progress.get("runtime") or None,
        "description": str(story.get("description") or ""),
        "acceptanceCriteria": [record["text"] for record in criteria_records],
        "criteria": criteria_records,
        "progressPercent": story_progress_percent(status, tasks),
        "assignee": _assignee(status, loop_state, tasks, progress),
        "qaVerdict": progress.get("qaVerdict") or None,
        "commit": progress.get("commit") or None,
        "runs": int(progress.get("runs") or 0),
        "tasks": [
            {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "role": str(task.get("role") or ""),
                "status": str(task.get("status") or ""),
            }
            for task in tasks
        ],
    }
