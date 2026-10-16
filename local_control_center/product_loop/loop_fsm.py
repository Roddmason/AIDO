"""FSM durable del product loop: crea loops, valida y persiste transiciones y aplica gobierno.

Colaborador de ``ProductLoopCoordinator`` extraído de ``coordinator.py``. Opera sobre la conexión y el
repositorio del coordinator; toda llamada a otra operación del loop (``transition``,
``transition_in_transaction``, ``_apply_transition``, ``_state_deadline``) se resuelve a través del
coordinator para conservar sus delegadores públicos y el parcheo por clase o por instancia.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.time import utc_now

from .fsm import (
    ALLOWED_TRANSITIONS,
    BLOCKED_STATE,
    CANCELLED_STATE,
    INITIAL_STATE,
    REWORK_STATE,
    TERMINAL_STATES,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
    deep_merge_fsm,
    empty_fsm,
    evaluate_stop_conditions,
    fsm_of,
    is_terminal,
    status_for,
)

if TYPE_CHECKING:
    from .coordinator import ProductLoopCoordinator
    from .repository import ProductLoopRepository

__all__ = ["ProductLoopFsm"]


class ProductLoopFsm:
    """Ciclo de vida durable de un product loop: alta, transiciones, bloqueo, consumo y reanudación."""

    def __init__(self, coordinator: ProductLoopCoordinator):
        self._coordinator = coordinator

    @property
    def connection(self) -> sqlite3.Connection:
        """Conexión del coordinator dueño de la transacción."""
        return self._coordinator.connection

    @property
    def repository(self) -> ProductLoopRepository:
        """Repositorio de loops del coordinator."""
        return self._coordinator.repository

    def start(
        self,
        *,
        project_id: str,
        title: str,
        initiative_id: str | None = None,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        budget: dict[str, Any] | None = None,
        timeouts: dict[str, Any] | None = None,
        max_rework_rounds: int | None = None,
        deadline: str | None = None,
        actor: str = "operator",
        reason: str = "Product loop started from a received goal.",
        now: str | None = None,
    ) -> dict[str, Any]:
        """Crea un loop en ``goal_received`` con su política de gobierno y registra la transición inicial.

        ``budget`` (límites por clave), ``timeouts`` (segundos por estado), ``max_rework_rounds``,
        ``deadline`` y ``correlation_id`` se persisten en ``context['fsm']`` para que el loop —y su
        gobierno— se reanude intacto tras un reinicio. El loop y su primera transición se confirman en una
        única transacción atómica.
        """
        now = now or utc_now()
        timeouts = timeouts or {}
        fsm = empty_fsm()
        fsm["correlationId"] = correlation_id
        fsm["policy"] = {
            "budget": dict(budget or {}),
            "timeouts": dict(timeouts),
            "maxReworkRounds": max_rework_rounds,
            "deadline": deadline,
        }
        fsm["usage"]["stateDeadline"] = self._coordinator._state_deadline(INITIAL_STATE, timeouts, now)
        loop_context = {**(context or {}), "fsm": fsm}
        with immediate_transaction(self.connection):
            loop = self.repository.create_loop(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "title": title,
                    "state": INITIAL_STATE,
                    "previousState": None,
                    "status": status_for(INITIAL_STATE),
                    "context": loop_context,
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
                    "metadata": {"correlationId": correlation_id},
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
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_version: int | None = None,
        context_patch: dict[str, Any] | None = None,
        now: str | None = None,
        _fsm_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Valida y aplica una transición durable; persiste estado, razón, correlation id y timestamp.

        Lee el estado durable, valida la transición y la versión, gestiona el bloque ``context['fsm']``
        (recalcula el deadline del estado de entrada, incrementa las rondas de rework al entrar a
        ``reworking`` y preserva presupuesto/consumo) y confirma el UPDATE del loop con el INSERT de la
        transición en una sola ``immediate_transaction``. ``context['fsm']`` lo gobierna este método: un
        ``fsm`` dentro de ``context_patch`` se ignora para no pisar el presupuesto/consumo del loop.

        Raises:
            KeyError: si el loop no existe.
            ProductLoopTransitionError: estado destino desconocido, transición no permitida desde el
                estado actual, o ``expected_version`` no coincide (o transición concurrente).
            ProductLoopStopConditionError: si entrar a ``reworking`` superaría ``maxReworkRounds``.
        """
        try:
            with immediate_transaction(self.connection):
                updated = self._coordinator.transition_in_transaction(
                    loop_id,
                    to_state=to_state,
                    reason=reason,
                    actor=actor,
                    trigger=trigger,
                    correlation_id=correlation_id,
                    metadata=metadata,
                    expected_version=expected_version,
                    context_patch=context_patch,
                    now=now,
                    _fsm_patch=_fsm_patch,
                )
        except sqlite3.IntegrityError as error:
            raise ProductLoopTransitionError(
                f"Concurrent product loop transition detected for product loop {loop_id}."
            ) from error
        return updated

    def transition_in_transaction(
        self,
        loop_id: str,
        *,
        to_state: str,
        reason: str = "",
        actor: str = "operator",
        trigger: str = "",
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_version: int | None = None,
        context_patch: dict[str, Any] | None = None,
        now: str | None = None,
        _fsm_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a transition and terminal remediation cleanup in the caller's transaction."""
        if not self.connection.in_transaction:
            raise RuntimeError("transition_in_transaction requires an active transaction.")
        updated, _transition = self._coordinator._apply_transition(
            loop_id,
            to_state=to_state,
            reason=reason,
            actor=actor,
            trigger=trigger,
            correlation_id=correlation_id,
            metadata=metadata,
            expected_version=expected_version,
            context_patch=context_patch,
            now=now,
            _fsm_patch=_fsm_patch,
        )
        if updated["state"] in TERMINAL_STATES:
            RemediationActionsRepository(self.connection).resolve_pending_for_loop_in_transaction(loop_id)
        return updated

    def _apply_transition(
        self,
        loop_id: str,
        *,
        to_state: str,
        reason: str = "",
        actor: str = "operator",
        trigger: str = "",
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_version: int | None = None,
        context_patch: dict[str, Any] | None = None,
        now: str | None = None,
        _fsm_patch: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Aplica una transición asumiendo que el caller ya abrió la transacción atómica."""
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
        now = now or utc_now()
        fsm = fsm_of(loop)
        if to_state == REWORK_STATE:
            max_rework = fsm["policy"].get("maxReworkRounds")
            next_rounds = int(fsm["usage"].get("reworkRounds", 0)) + 1
            if max_rework is not None and next_rounds > int(max_rework):
                raise ProductLoopStopConditionError(
                    f"Maximum rework rounds ({max_rework}) reached for product loop {loop_id}."
                )
            fsm["usage"]["reworkRounds"] = next_rounds
        fsm["usage"]["stateDeadline"] = self._coordinator._state_deadline(
            to_state, fsm["policy"].get("timeouts"), now
        )
        if _fsm_patch:
            fsm = deep_merge_fsm(fsm, _fsm_patch)
        effective_correlation = correlation_id if correlation_id is not None else fsm.get("correlationId")
        next_version = loop["version"] + 1
        context = {**loop["context"], **(context_patch or {})}
        context["fsm"] = fsm
        transition_metadata = {**(metadata or {}), "correlationId": effective_correlation}
        updated = self.repository.update_loop_state(
            loop_id,
            state=to_state,
            previous_state=current,
            status=status_for(to_state),
            context=context,
            version=next_version,
        )
        transition = self.repository.create_transition(
            {
                "loopId": loop_id,
                "projectId": loop["projectId"],
                "fromState": current,
                "toState": to_state,
                "reason": reason,
                "actor": actor,
                "trigger": trigger or to_state,
                "version": next_version,
                "metadata": transition_metadata,
            }
        )
        return updated, transition

    def block(
        self,
        loop_id: str,
        *,
        reason: str,
        actor: str = "operator",
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Bloquea el loop (transición a ``blocked``) desde cualquier estado no terminal."""
        return self._coordinator.transition(
            loop_id,
            to_state=BLOCKED_STATE,
            reason=reason,
            actor=actor,
            trigger="blocked",
            metadata=metadata,
            correlation_id=correlation_id,
            now=now,
        )

    def cancel(
        self,
        loop_id: str,
        *,
        reason: str,
        actor: str = "operator",
        correlation_id: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Cancela el loop (transición terminal a ``cancelled``) desde cualquier estado no terminal.

        Raises:
            KeyError: si el loop no existe.
            ProductLoopTransitionError: si el loop ya es terminal (delivered/cancelled).
        """
        return self._coordinator.transition(
            loop_id,
            to_state=CANCELLED_STATE,
            reason=reason,
            actor=actor,
            trigger="cancelled",
            correlation_id=correlation_id,
            now=now,
        )

    def unblock(
        self,
        loop_id: str,
        *,
        to_state: str | None = None,
        reason: str = "Product loop unblocked.",
        actor: str = "operator",
        now: str | None = None,
    ) -> dict[str, Any]:
        """Reanuda un loop bloqueado hacia ``to_state`` (o su estado previo si no se indica).

        El default seguro es el ``previousState`` (retoma el trabajo donde quedó). Indicar otro destino
        reanudable es válido pero puede descartar progreso, por lo que debe ser una decisión explícita
        del caller.

        Raises:
            KeyError: si el loop no existe.
            ProductLoopTransitionError: si el loop no está bloqueado o el destino no es reanudable.
        """
        loop = self.repository.get_loop(loop_id)
        if loop["state"] != BLOCKED_STATE:
            raise ProductLoopTransitionError("Product loop is not blocked.")
        target = to_state or loop["previousState"] or INITIAL_STATE
        return self._coordinator.transition(
            loop_id, to_state=target, reason=reason, actor=actor, trigger="unblocked", now=now
        )

    def record_usage(
        self, loop_id: str, usage_delta: dict[str, Any], *, expected_usage_seq: int | None = None
    ) -> dict[str, Any]:
        """Acumula consumo de presupuesto en ``context['fsm']['usage']['consumed']`` sin tocar la FSM.

        Camino de medición independiente: NO incrementa la versión de la FSM ni inserta una transición,
        para que la bitácora quede limpia de métricas. Lee-modifica-escribe el contexto bajo una
        ``immediate_transaction`` y ``expected_usage_seq`` ofrece un guard optimista sobre el consumo.

        Raises:
            KeyError: si el loop no existe.
            ProductLoopTransitionError: si ``expected_usage_seq`` no coincide con el contador actual.
        """
        with immediate_transaction(self.connection):
            loop = self.repository.get_loop(loop_id)
            fsm = fsm_of(loop)
            current_seq = int(fsm["usage"].get("usageSeq", 0))
            if expected_usage_seq is not None and int(expected_usage_seq) != current_seq:
                raise ProductLoopTransitionError(
                    f"Product loop usage version mismatch: expected {expected_usage_seq}, found {current_seq}."
                )
            consumed = dict(fsm["usage"].get("consumed") or {})
            for key, value in (usage_delta or {}).items():
                consumed[key] = float(consumed.get(key, 0)) + float(value)
            fsm["usage"]["consumed"] = consumed
            fsm["usage"]["usageSeq"] = current_seq + 1
            return self.repository.update_loop_context(loop_id, context={**loop["context"], "fsm": fsm})

    def evaluate_stop_conditions(self, loop_id: str, *, now: str | None = None) -> list[dict[str, Any]]:
        """Devuelve las condiciones de parada activas del loop (lectura pura sobre su estado durable)."""
        return evaluate_stop_conditions(self.repository.get_loop(loop_id), now=now)

    def enforce_stop_conditions(
        self, loop_id: str, *, now: str | None = None, actor: str = "system"
    ) -> dict[str, Any]:
        """Si alguna condición de parada está activa y el loop sigue activo, lo bloquea con su motivo.

        Idempotente: si el loop ya es terminal o está bloqueado, no hace nada. Si una transición
        concurrente cambió el estado entre la lectura y el bloqueo, re-lee y devuelve el loop sin
        relanzar (la condición ya quedó atendida). Devuelve ``{loop, fired}`` con las condiciones que
        dispararon.
        """
        now = now or utc_now()
        loop = self.repository.get_loop(loop_id)
        if loop["state"] in TERMINAL_STATES or loop["state"] == BLOCKED_STATE:
            return {"loop": loop, "fired": []}
        fired = evaluate_stop_conditions(loop, now=now)
        if not fired:
            return {"loop": loop, "fired": []}
        reason = "Stop condition(s) reached: " + ", ".join(item["condition"] for item in fired)
        try:
            updated = self._coordinator.transition(
                loop_id,
                to_state=BLOCKED_STATE,
                reason=reason,
                actor=actor,
                trigger="stop_condition",
                metadata={"stopConditions": fired},
                now=now,
                _fsm_patch={"usage": {"stoppedReason": reason}},
            )
        except ProductLoopTransitionError:
            return {"loop": self.repository.get_loop(loop_id), "fired": fired}
        return {"loop": updated, "fired": fired}

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
