"""Gate: el orden de estados del stepper (frontend) cubre todos los estados del FSM del backend.

El stepper del Workbench rankea el progreso con ``PRODUCT_LOOP_STATE_ORDER.indexOf(loop.state)``; si un
estado real del backend falta en esa lista, ``indexOf`` devuelve ``-1`` y el stepper entero se pinta como
"upcoming" aunque el loop esté avanzado. Este gate falla si el backend agrega un estado que el frontend no
lista, evitando que la lista vuelva a derivar en silencio.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from pathlib import Path

from local_control_center.product_loop.coordinator import PRODUCT_LOOP_STATES

ROOT = Path(__file__).resolve().parents[1]
MODEL_TS = ROOT / "local-control-center" / "web" / "src" / "features" / "workbench" / "productLoopModel.ts"


def _frontend_state_order() -> list[str]:
    source = MODEL_TS.read_text(encoding="utf-8")
    match = re.search(r"PRODUCT_LOOP_STATE_ORDER\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    assert match, "PRODUCT_LOOP_STATE_ORDER array literal not found in productLoopModel.ts"
    return re.findall(r"'([a-z_]+)'", match.group(1))


def test_product_loop_state_order_covers_backend_states() -> None:
    frontend = _frontend_state_order()
    missing = [state for state in PRODUCT_LOOP_STATES if state not in frontend]
    assert not missing, f"Frontend PRODUCT_LOOP_STATE_ORDER is missing backend states: {missing}"


def test_product_loop_state_order_lists_no_phantom_states() -> None:
    frontend = _frontend_state_order()
    phantom = [state for state in frontend if state not in PRODUCT_LOOP_STATES]
    assert not phantom, (
        f"Frontend PRODUCT_LOOP_STATE_ORDER lists states the backend does not define: {phantom}"
    )
