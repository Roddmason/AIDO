"""Gate: la matriz de transiciones del product loop sigue el flujo spec-driven sin bypass de gates.

Ancla las tres propiedades del slice 0 del diseño spec-driven (docs/superpowers/specs/
2026-07-28-aido-spec-driven-process-design.md §4.1): el camino feliz completo es legal de punta a
punta (incluida la arista ``iteration_planning -> branch_ready`` que faltaba y dejaba el loop sin
poder asignar worktree tras aprobar el backlog), ``quality_review`` corre después de Security con el
diff disponible, y no existe ningún camino de ``executing`` a ``awaiting_approval`` que evite
``qa_running`` y ``security_running``. El orden declarado de estados debe reflejar ese flujo porque
el stepper del frontend rankea por índice.

@author Rodrigo Mason
"""

from __future__ import annotations

from itertools import pairwise

from local_control_center.product_loop.coordinator import (
    ALLOWED_TRANSITIONS,
    PRODUCT_LOOP_STATES,
    TERMINAL_STATES,
)

HAPPY_PATH = [
    "goal_received",
    "workspace_check",
    "runtime_check",
    "git_check",
    "discovery",
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
    "delivered",
]

# Estados de control que pueden aparecer en cualquier tramo y no participan del orden de flujo.
_OFF_PATH = {"blocked", "cancelled", "awaiting_feedback", "reworking"}


def test_happy_path_is_fully_legal() -> None:
    """Cada arista consecutiva del camino feliz está permitida por la FSM."""
    illegal = [f"{a} -> {b}" for a, b in pairwise(HAPPY_PATH) if b not in ALLOWED_TRANSITIONS[a]]
    assert not illegal, f"Happy-path transitions missing from ALLOWED_TRANSITIONS: {illegal}"


def test_iteration_planning_reaches_branch_ready() -> None:
    """Aprobar el backlog (que transiciona a iteration_planning) no puede dejar el loop varado."""
    assert "branch_ready" in ALLOWED_TRANSITIONS["iteration_planning"]


def test_no_path_from_executing_skips_qa_and_security() -> None:
    """Todo camino de flujo executing -> awaiting_approval pasa por qa_running Y security_running.

    ``blocked`` se trata como absorbente: su fan-out a ``_RESUMABLE_STATES`` es el mecanismo
    explícito de recuperación del operador (unblock), no una arista del pipeline automático.
    """
    for forbidden in ("qa_running", "security_running"):
        seen: set[str] = set()
        frontier = ["executing"]
        while frontier:
            state = frontier.pop()
            if state in seen or state in {forbidden, "blocked"}:
                continue
            seen.add(state)
            frontier.extend(ALLOWED_TRANSITIONS.get(state, set()) - TERMINAL_STATES)
        assert "awaiting_approval" not in seen, (
            f"awaiting_approval is reachable from executing without passing through {forbidden}"
        )


def test_declared_state_order_matches_happy_path_flow() -> None:
    """PRODUCT_LOOP_STATES lista los estados del camino feliz en su orden real de ejecución."""
    ranked = [state for state in PRODUCT_LOOP_STATES if state in HAPPY_PATH]
    on_path = [state for state in HAPPY_PATH if state in ranked]
    assert ranked == on_path, f"Declared order {ranked} diverges from flow order {on_path}"


def test_every_state_is_reachable_and_can_terminate() -> None:
    """Ningún estado queda huérfano: todos se alcanzan desde el inicio y alcanzan un terminal."""
    seen: set[str] = set()
    frontier = ["goal_received"]
    while frontier:
        state = frontier.pop()
        if state in seen:
            continue
        seen.add(state)
        frontier.extend(ALLOWED_TRANSITIONS.get(state, set()))
    unreachable = set(PRODUCT_LOOP_STATES) - seen
    assert not unreachable, f"States unreachable from goal_received: {unreachable}"

    for state in PRODUCT_LOOP_STATES:
        if state in TERMINAL_STATES:
            continue
        seen = set()
        frontier = [state]
        terminates = False
        while frontier and not terminates:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in TERMINAL_STATES:
                terminates = True
                break
            frontier.extend(ALLOWED_TRANSITIONS.get(current, set()))
        assert terminates, f"State {state} cannot reach any terminal state"
