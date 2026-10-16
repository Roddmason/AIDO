"""Máquina de estados pura del product loop: estados, transiciones permitidas y condiciones de parada.

Sin I/O ni acceso a la base: el coordinador persiste; este módulo solo decide qué es válido y qué
condiciones de parada están activas para un loop dado.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.shared.time import utc_now

PRODUCT_LOOP_STATES = [
    "goal_received",
    "workspace_check",
    "runtime_check",
    "git_check",
    "discovery",
    "discovering",
    "awaiting_user",
    "planning",
    "brief_ready",
    "architecture_review",
    "backlog_ready",
    "iteration_planning",
    "branch_ready",
    "executing",
    "qa_running",
    "security_running",
    "quality_review",
    "review_ready",
    "awaiting_approval",
    "awaiting_feedback",
    "reworking",
    "delivered",
    "blocked",
    "cancelled",
]
INITIAL_STATE = "goal_received"
DELIVERED_STATE = "delivered"
CANCELLED_STATE = "cancelled"
BLOCKED_STATE = "blocked"
REWORK_STATE = "reworking"
DEFAULT_AUTO_REWORK_ROUNDS = 2
AWAITING_FEEDBACK_STATE = "awaiting_feedback"
TERMINAL_STATES = {DELIVERED_STATE, CANCELLED_STATE}
RESUMABLE_STATES = {state for state in PRODUCT_LOOP_STATES if state not in TERMINAL_STATES | {BLOCKED_STATE}}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "goal_received": {"workspace_check", "discovery", "discovering", "blocked", "cancelled"},
    "workspace_check": {"runtime_check", "git_check", "blocked", "cancelled"},
    "runtime_check": {"git_check", "discovery", "blocked", "cancelled"},
    "git_check": {"runtime_check", "discovery", "blocked", "cancelled"},
    "discovery": {"awaiting_user", "planning", "brief_ready", "backlog_ready", "blocked", "cancelled"},
    "discovering": {"awaiting_user", "brief_ready", "blocked", "cancelled"},
    "awaiting_user": {"discovering", "brief_ready", "blocked", "cancelled"},
    "planning": {"architecture_review", "backlog_ready", "blocked", "cancelled"},
    "brief_ready": {"architecture_review", "backlog_ready", "blocked", "cancelled"},
    "architecture_review": {"backlog_ready", "brief_ready", "blocked", "cancelled"},
    "backlog_ready": {"branch_ready", "iteration_planning", "blocked", "cancelled"},
    "branch_ready": {"executing", "blocked", "cancelled"},
    "iteration_planning": {"branch_ready", "executing", "blocked", "cancelled"},
    "executing": {"qa_running", "blocked", "cancelled"},
    "qa_running": {"security_running", "reworking", "executing", "blocked", "cancelled"},
    "security_running": {"quality_review", "review_ready", "blocked", "cancelled"},
    "review_ready": {"awaiting_approval", "reworking", "blocked", "cancelled"},
    "quality_review": {"review_ready", "awaiting_approval", "reworking", "blocked", "cancelled"},
    "awaiting_approval": {"delivered", "reworking", "awaiting_feedback", "blocked", "cancelled"},
    "awaiting_feedback": {"reworking", "executing", "blocked", "cancelled"},
    "reworking": {"executing", "blocked", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
    "blocked": set(RESUMABLE_STATES) | {CANCELLED_STATE},
}


class ProductLoopTransitionError(ValueError):
    """Se lanza ante un estado desconocido, una transición no permitida o un desfase de versión."""


class ProductLoopStopConditionError(ValueError):
    """Se lanza cuando una política de parada impide la transición (p. ej. máximo de rework alcanzado)."""


def is_terminal(state: str) -> bool:
    """Indica si un estado es terminal (no admite más transiciones)."""
    return state in TERMINAL_STATES


def allowed_next_states(state: str) -> set[str]:
    """Devuelve el conjunto de estados a los que se puede transicionar desde ``state``.

    Raises:
        ProductLoopTransitionError: si ``state`` no es un estado válido del product loop.
    """
    if state not in ALLOWED_TRANSITIONS:
        raise ProductLoopTransitionError(f"Unknown product loop state: {state}")
    return set(ALLOWED_TRANSITIONS[state])


def status_for(state: str) -> str:
    """Estado agregado (``delivered``/``cancelled``/``blocked``/``active``) de un estado del loop."""
    if state == DELIVERED_STATE:
        return "delivered"
    if state == CANCELLED_STATE:
        return "cancelled"
    if state == BLOCKED_STATE:
        return "blocked"
    return "active"


def empty_fsm() -> dict[str, Any]:
    """Bloque ``context['fsm']`` vacío con la política y el consumo por defecto."""
    return {
        "correlationId": None,
        "policy": {"budget": {}, "timeouts": {}, "maxReworkRounds": None, "deadline": None},
        "usage": {
            "consumed": {},
            "reworkRounds": 0,
            "stateDeadline": None,
            "stoppedReason": None,
            "usageSeq": 0,
        },
    }


def fsm_of(loop: dict[str, Any]) -> dict[str, Any]:
    """Proyecta el bloque ``context['fsm']`` del loop con todos sus defaults (copia segura de mutar)."""
    raw = (loop.get("context") or {}).get("fsm") or {}
    base = empty_fsm()
    return {
        "correlationId": raw.get("correlationId"),
        "policy": {**base["policy"], **(raw.get("policy") or {})},
        "usage": {**base["usage"], **(raw.get("usage") or {})},
    }


def deep_merge_fsm(fsm: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Mezcla ``patch`` sobre ``fsm`` con un nivel de profundidad en los sub-dicts (policy/usage)."""
    result = {**fsm}
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def evaluate_stop_conditions(loop: dict[str, Any], *, now: str | None = None) -> list[dict[str, Any]]:
    """Evalúa de forma pura qué condiciones de parada están activas para ``loop`` en el instante ``now``.

    Una condición solo se dispara si su límite está configurado y se excedió: presupuesto agotado (algún
    consumido ≥ su límite), deadline global vencido, timeout del estado actual vencido, o máximo de rondas
    de rework alcanzado (rondas consumidas ≥ máximo: la transición a ``reworking`` ya no admite otra, porque
    rechaza ``rondas + 1 > máximo``). Devuelve una lista de ``{condition, detail}`` (vacía si ninguna).
    """
    now = now or utc_now()
    fsm = fsm_of(loop)
    policy, usage = fsm["policy"], fsm["usage"]
    fired: list[dict[str, Any]] = []
    consumed = usage.get("consumed") or {}
    for key, limit in (policy.get("budget") or {}).items():
        if isinstance(limit, (int, float)) and float(consumed.get(key, 0)) >= float(limit):
            fired.append({"condition": "budget_exhausted", "detail": f"{key} reached its budget ({limit})."})
    deadline = policy.get("deadline")
    if deadline and now > deadline:
        fired.append({"condition": "deadline_exceeded", "detail": f"Loop deadline {deadline} passed."})
    state_deadline = usage.get("stateDeadline")
    if state_deadline and now > state_deadline:
        fired.append({"condition": "state_timeout", "detail": f"State deadline {state_deadline} passed."})
    max_rework = policy.get("maxReworkRounds")
    if max_rework is not None and int(usage.get("reworkRounds", 0)) >= int(max_rework):
        fired.append({"condition": "max_rework_reached", "detail": f"Reached {max_rework} rework round(s)."})
    return fired
