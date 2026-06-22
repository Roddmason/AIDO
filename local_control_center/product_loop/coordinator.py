"""Máquina de estados durable del product loop: valida transiciones y las persiste atómicamente.

Coordina el ciclo idea → discovery → brief → arquitectura → backlog → planning → iteración → calidad →
entrega mediante una FSM cuyo estado vive en la base (no solo en memoria). El coordinador no guarda
estado en memoria: cada operación lee el loop durable del repositorio, valida la transición contra el
mapa permitido y confirma el nuevo estado junto con su registro en una única transacción atómica. Por
eso el loop puede reanudarse tal cual tras reiniciar AIDO: basta construir un coordinador sobre una
conexión nueva y leer el estado persistido.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.shared.db import immediate_transaction

from .repository import ProductLoopRepository

PRODUCT_LOOP_STATES = [
    "idea_received",
    "discovery_running",
    "awaiting_user",
    "brief_ready",
    "awaiting_architecture_decision",
    "backlog_draft",
    "backlog_review",
    "ready_for_planning",
    "iteration_running",
    "quality_review",
    "awaiting_feedback",
    "completed",
    "blocked",
]
INITIAL_STATE = "idea_received"
BLOCKED_STATE = "blocked"
TERMINAL_STATES = {"completed"}
# Estados activos a los que un loop bloqueado puede regresar para reanudarse.
_RESUMABLE_STATES = {state for state in PRODUCT_LOOP_STATES if state not in TERMINAL_STATES | {BLOCKED_STATE}}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "idea_received": {"discovery_running", "blocked"},
    "discovery_running": {"awaiting_user", "brief_ready", "blocked"},
    "awaiting_user": {"discovery_running", "brief_ready", "blocked"},
    "brief_ready": {"awaiting_architecture_decision", "backlog_draft", "blocked"},
    "awaiting_architecture_decision": {"backlog_draft", "brief_ready", "blocked"},
    "backlog_draft": {"backlog_review", "blocked"},
    "backlog_review": {"ready_for_planning", "backlog_draft", "blocked"},
    "ready_for_planning": {"iteration_running", "blocked"},
    "iteration_running": {"quality_review", "blocked"},
    "quality_review": {"awaiting_feedback", "completed", "iteration_running", "blocked"},
    "awaiting_feedback": {"iteration_running", "completed", "blocked"},
    "completed": set(),
    "blocked": set(_RESUMABLE_STATES),
}


class ProductLoopTransitionError(ValueError):
    """Se lanza ante un estado desconocido, una transición no permitida o un desfase de versión."""


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


def _status_for(state: str) -> str:
    if state in TERMINAL_STATES:
        return "completed"
    if state == BLOCKED_STATE:
        return "blocked"
    return "active"


class ProductLoopCoordinator:
    """Coordina un product loop como FSM durable: arranca, transiciona y reanuda desde la base.

    No mantiene estado en memoria: solo guarda la conexión/repositorio y lee el loop persistido en cada
    operación, por lo que un coordinador nuevo (tras reiniciar AIDO) reanuda el loop tal como quedó.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.repository = ProductLoopRepository(connection)

    def start(
        self,
        *,
        project_id: str,
        title: str,
        initiative_id: str | None = None,
        context: dict[str, Any] | None = None,
        actor: str = "operator",
        reason: str = "Product loop started from a received idea.",
    ) -> dict[str, Any]:
        """Crea un loop en ``idea_received`` y registra su transición inicial de forma atómica."""
        with immediate_transaction(self.connection):
            loop = self.repository.create_loop(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "title": title,
                    "state": INITIAL_STATE,
                    "previousState": None,
                    "status": _status_for(INITIAL_STATE),
                    "context": context or {},
                }
            )
            self.repository.create_transition(
                {
                    "loopId": loop["id"],
                    "projectId": project_id,
                    "fromState": "",
                    "toState": INITIAL_STATE,
                    "reason": reason,
                    "actor": actor,
                    "trigger": "started",
                    "version": loop["version"],
                }
            )
        return loop

    def get(self, loop_id: str) -> dict[str, Any]:
        """Lee el loop durable por id.

        Raises:
            KeyError: si no existe ningún loop con ese id.
        """
        return self.repository.get_loop(loop_id)

    def list_loops(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista loops (todos o por proyecto), el más recientemente actualizado primero."""
        return self.repository.list_loops(project_id)

    def list_transitions(self, loop_id: str) -> list[dict[str, Any]]:
        """Devuelve la bitácora de transiciones del loop en orden cronológico."""
        return self.repository.list_transitions(loop_id)

    def transition(
        self,
        loop_id: str,
        *,
        to_state: str,
        reason: str = "",
        actor: str = "operator",
        trigger: str = "",
        metadata: dict[str, Any] | None = None,
        expected_version: int | None = None,
        context_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Valida y aplica una transición durable; persiste el nuevo estado y su registro atómicamente.

        Lee el estado durable, comprueba que la transición esté permitida (y la versión si se exige),
        incrementa la versión, fusiona ``context_patch`` y confirma el UPDATE del loop con el INSERT de
        la transición en una sola ``immediate_transaction``.

        Raises:
            KeyError: si el loop no existe.
            ProductLoopTransitionError: si el estado destino es desconocido, la transición no está
                permitida desde el estado actual, o ``expected_version`` no coincide (o hay una
                transición concurrente con la misma versión).
        """
        if to_state not in ALLOWED_TRANSITIONS:
            raise ProductLoopTransitionError(f"Unknown product loop state: {to_state}")
        loop = self.repository.get_loop(loop_id)
        current = loop["state"]
        if expected_version is not None and int(expected_version) != loop["version"]:
            raise ProductLoopTransitionError(
                f"Product loop version mismatch: expected {expected_version}, found {loop['version']}."
            )
        if to_state not in ALLOWED_TRANSITIONS[current]:
            raise ProductLoopTransitionError(f"Invalid product loop transition: {current} -> {to_state}.")
        next_version = loop["version"] + 1
        context = {**loop["context"], **(context_patch or {})}
        try:
            with immediate_transaction(self.connection):
                updated = self.repository.update_loop_state(
                    loop_id,
                    state=to_state,
                    previous_state=current,
                    status=_status_for(to_state),
                    context=context,
                    version=next_version,
                )
                self.repository.create_transition(
                    {
                        "loopId": loop_id,
                        "projectId": loop["projectId"],
                        "fromState": current,
                        "toState": to_state,
                        "reason": reason,
                        "actor": actor,
                        "trigger": trigger or to_state,
                        "version": next_version,
                        "metadata": metadata,
                    }
                )
        except sqlite3.IntegrityError as error:
            raise ProductLoopTransitionError(
                f"Concurrent product loop transition detected at version {next_version}."
            ) from error
        return updated

    def block(
        self,
        loop_id: str,
        *,
        reason: str,
        actor: str = "operator",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bloquea el loop (transición a ``blocked``) desde cualquier estado no terminal."""
        return self.transition(
            loop_id, to_state=BLOCKED_STATE, reason=reason, actor=actor, trigger="blocked", metadata=metadata
        )

    def unblock(
        self,
        loop_id: str,
        *,
        to_state: str | None = None,
        reason: str = "Product loop unblocked.",
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Reanuda un loop bloqueado hacia ``to_state`` (o su estado previo si no se indica).

        Raises:
            KeyError: si el loop no existe.
            ProductLoopTransitionError: si el loop no está bloqueado o el destino no es reanudable.
        """
        loop = self.repository.get_loop(loop_id)
        if loop["state"] != BLOCKED_STATE:
            raise ProductLoopTransitionError("Product loop is not blocked.")
        target = to_state or loop["previousState"] or INITIAL_STATE
        return self.transition(loop_id, to_state=target, reason=reason, actor=actor, trigger="unblocked")

    def resume(self, loop_id: str) -> dict[str, Any]:
        """Reanuda un loop leyendo su estado durable y devuelve qué transiciones admite ahora.

        Pensado para usarse tras reiniciar AIDO: el estado proviene íntegramente de la base.

        Raises:
            KeyError: si no existe ningún loop con ese id.
        """
        loop = self.repository.get_loop(loop_id)
        return {
            "loop": loop,
            "resumable": not is_terminal(loop["state"]),
            "allowedNextStates": sorted(ALLOWED_TRANSITIONS[loop["state"]]),
            "transitions": self.repository.list_transitions(loop_id),
        }
