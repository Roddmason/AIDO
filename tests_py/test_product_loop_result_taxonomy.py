"""Gate: cada status de resultado del product loop mapea a propósito, sin defaults silenciosos.

Slice 1 del diseño spec-driven (docs/superpowers/specs/2026-07-28 §9). Tres taxonomías deben cubrir
el mismo conjunto de statuses que ``_run_user_message`` puede retornar: el veredicto de evidencia de
``_record_run_evidence`` (un status sano no puede caer al default ``blocked``/severidad high), el
ruteo del worker a estado de hilo + tipo de evento (un ``brief_ready`` sano pintaba el pipeline
entero en rojo porque el default era el evento ``blocked``), y el ``MILESTONE_INDEX`` del panel de
ejecución (un estado sin entrada congela el stepper en el hito anterior). Si el coordinator gana un
status nuevo, este gate obliga a decidir su ruteo en el mismo commit.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from pathlib import Path

from local_control_center.jobs_approvals.worker import PRODUCT_LOOP_RESULT_ROUTING
from local_control_center.product_loop.coordinator import (
    PRODUCT_LOOP_STATES,
    RUN_EVIDENCE_PASSED_STATUSES,
    RUN_RESULT_STATUSES,
)

ROOT = Path(__file__).resolve().parents[1]
PANEL_TSX = ROOT / "local-control-center" / "web" / "src" / "features" / "shell" / "ThreadExecutionPanel.tsx"

# El worker sintetiza "failed" cuando el resultado no trae status (worker.py: str(result.get("status") or "failed")).
WORKER_SYNTHETIC_STATUSES = {"failed"}

# Estados del FSM que por diseño no son hitos del pipeline de ejecución: el inicio es implícito,
# blocked/cancelled se pintan por su propio canal (isBlocked / status del hilo) y awaiting_feedback
# es una espera del operador sin nodo propio.
MILESTONE_EXEMPT_STATES = {"goal_received", "blocked", "cancelled", "awaiting_feedback"}


def test_worker_routing_covers_every_run_result_status() -> None:
    """Todo status que el coordinator retorna tiene ruteo explícito (hilo, evento) en el worker."""
    missing = [
        status
        for status in (*RUN_RESULT_STATUSES, *WORKER_SYNTHETIC_STATUSES)
        if status not in PRODUCT_LOOP_RESULT_ROUTING
    ]
    assert not missing, f"Worker routing relies on the fallback for statuses: {missing}"


def test_worker_routing_has_no_phantom_statuses() -> None:
    """El ruteo del worker no lista statuses que el coordinator ya no produce."""
    known = set(RUN_RESULT_STATUSES) | WORKER_SYNTHETIC_STATUSES | {"delivered"}
    phantom = [status for status in PRODUCT_LOOP_RESULT_ROUTING if status not in known]
    assert not phantom, f"Worker routing lists unknown statuses: {phantom}"


def test_healthy_statuses_never_emit_a_blocked_event() -> None:
    """Los cierres sanos (brief listo, plan listo, espera de decisión) no pintan el pipeline en rojo."""
    for status in (
        "brief_ready",
        "plan_ready",
        "awaiting_user",
        "awaiting_approval",
        "completed",
        "cancelled",
    ):
        _thread_status, event_type = PRODUCT_LOOP_RESULT_ROUTING[status]
        assert event_type != "blocked", f"Healthy status {status} routes to a blocked event"


def test_evidence_verdict_is_deliberate_for_every_run_result_status() -> None:
    """Cada status de resultado cae explícitamente en 'passed' o en el lado bloqueado del veredicto."""
    blocked_side = {"blocked", "failed", "cancelled"}
    undecided = [
        status
        for status in RUN_RESULT_STATUSES
        if status not in RUN_EVIDENCE_PASSED_STATUSES and status not in blocked_side
    ]
    assert not undecided, (
        f"Statuses fall to the blocked/high-severity default without a deliberate decision: {undecided}"
    )


def _milestone_index_keys() -> set[str]:
    source = PANEL_TSX.read_text(encoding="utf-8")
    match = re.search(r"const MILESTONE_INDEX: Record<string, number> = \{(.*?)\};", source, re.DOTALL)
    assert match, "MILESTONE_INDEX literal not found in ThreadExecutionPanel.tsx"
    return set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.MULTILINE))


def test_milestone_index_covers_every_fsm_state() -> None:
    """Todo estado del FSM tiene hito en el panel de ejecución o está en la lista de exentos."""
    keys = _milestone_index_keys()
    missing = [
        state for state in PRODUCT_LOOP_STATES if state not in keys and state not in MILESTONE_EXEMPT_STATES
    ]
    assert not missing, f"MILESTONE_INDEX is missing FSM states (stepper would freeze): {missing}"
