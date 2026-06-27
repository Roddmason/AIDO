"""Máquina de estados durable del product loop: valida transiciones, las persiste y aplica gobierno.

Coordina el ciclo ``goal_received → discovering → … → delivered`` (o ``cancelled``/``blocked``) mediante
una FSM determinista cuyo estado vive en la base —no solo en memoria—: cada operación lee el loop
durable del repositorio, valida la transición contra el mapa permitido, comprueba la versión optimista y
confirma el nuevo estado junto con su registro (razón, actor, trigger, correlation id y timestamp) en una
única transacción atómica. Por eso el loop se reanuda tal cual tras reiniciar AIDO: basta construir un
coordinador sobre una conexión nueva y leer el estado persistido.

Sobre la FSM pura aplica el gobierno del loop —presupuesto, timeouts por estado, máximo de rondas de
rework, deadline global y condiciones de parada—. La política y su consumo viven en ``context['fsm']``
(estado del agregado loop, resuelto atómicamente con él); el consumo de presupuesto se acumula por un
camino propio (``record_usage``) que NO incrementa la versión de la FSM ni inserta una transición, para
no ensuciar la bitácora con métricas. Las condiciones de parada se evalúan de forma determinista bajo
demanda (no hay scheduler en segundo plano) e inyectando ``now`` para que los tests sean reproducibles.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import iso_after_seconds, utc_now

from .models import FEEDBACK_ACTION_VALUES, FEEDBACK_CLASSIFICATION_VALUES
from .repository import ProductLoopRepository

PRODUCT_LOOP_STATES = [
    "goal_received",
    "discovering",
    "awaiting_user",
    "brief_ready",
    "architecture_review",
    "backlog_ready",
    "iteration_planning",
    "executing",
    "quality_review",
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
TERMINAL_STATES = {DELIVERED_STATE, CANCELLED_STATE}
_RESUMABLE_STATES = {state for state in PRODUCT_LOOP_STATES if state not in TERMINAL_STATES | {BLOCKED_STATE}}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "goal_received": {"discovering", "blocked", "cancelled"},
    "discovering": {"awaiting_user", "brief_ready", "blocked", "cancelled"},
    "awaiting_user": {"discovering", "brief_ready", "blocked", "cancelled"},
    "brief_ready": {"architecture_review", "backlog_ready", "blocked", "cancelled"},
    "architecture_review": {"backlog_ready", "brief_ready", "blocked", "cancelled"},
    "backlog_ready": {"iteration_planning", "blocked", "cancelled"},
    "iteration_planning": {"executing", "blocked", "cancelled"},
    "executing": {"quality_review", "blocked", "cancelled"},
    "quality_review": {"awaiting_approval", "reworking", "blocked", "cancelled"},
    "awaiting_approval": {"delivered", "reworking", "awaiting_feedback", "blocked", "cancelled"},
    "awaiting_feedback": {"reworking", "executing", "blocked", "cancelled"},
    "reworking": {"executing", "quality_review", "blocked", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
    "blocked": set(_RESUMABLE_STATES) | {CANCELLED_STATE},
}

STOP_CONDITIONS = ("budget_exhausted", "deadline_exceeded", "state_timeout", "max_rework_reached")
FEEDBACK_ACTIONS = set(FEEDBACK_ACTION_VALUES)
FEEDBACK_CLASSIFICATIONS = set(FEEDBACK_CLASSIFICATION_VALUES)
TARGET_CLASSIFICATIONS = {
    "task": "rework_task",
    "story": "new_story",
    "epic": "new_epic",
    "brief": "brief_revision",
    "loop": "brief_revision",
    "decision": "architecture_revision",
    "architecture": "architecture_revision",
}
TARGET_REQUIRED_ACTIONS = {"request_changes", "reprioritize", "reject_decision", "reopen_story"}


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


def _status_for(state: str) -> str:
    if state == DELIVERED_STATE:
        return "delivered"
    if state == CANCELLED_STATE:
        return "cancelled"
    if state == BLOCKED_STATE:
        return "blocked"
    return "active"


def _empty_fsm() -> dict[str, Any]:
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


def _fsm_of(loop: dict[str, Any]) -> dict[str, Any]:
    """Proyecta el bloque ``context['fsm']`` del loop con todos sus defaults (copia segura de mutar)."""
    raw = (loop.get("context") or {}).get("fsm") or {}
    base = _empty_fsm()
    return {
        "correlationId": raw.get("correlationId"),
        "policy": {**base["policy"], **(raw.get("policy") or {})},
        "usage": {**base["usage"], **(raw.get("usage") or {})},
    }


def _deep_merge_fsm(fsm: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Mezcla ``patch`` sobre ``fsm`` con un nivel de profundidad en los sub-dicts (policy/usage)."""
    result = {**fsm}
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def _normalize_key(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _append_feedback_id(metadata: dict[str, Any] | None, feedback_id: str) -> dict[str, Any]:
    result = dict(metadata or {})
    feedback_ids = [str(item) for item in result.get("feedbackIds", []) if str(item).strip()]
    if feedback_id not in feedback_ids:
        feedback_ids.append(feedback_id)
    result["feedbackIds"] = feedback_ids
    return result


def evaluate_stop_conditions(loop: dict[str, Any], *, now: str | None = None) -> list[dict[str, Any]]:
    """Evalúa de forma pura qué condiciones de parada están activas para ``loop`` en el instante ``now``.

    Una condición solo se dispara si su límite está configurado y se excedió: presupuesto agotado (algún
    consumido ≥ su límite), deadline global vencido, timeout del estado actual vencido, o máximo de rondas
    de rework alcanzado. Devuelve una lista de ``{condition, detail}`` (vacía si ninguna).
    """
    now = now or utc_now()
    fsm = _fsm_of(loop)
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


class ProductLoopCoordinator:
    """Coordina un product loop como FSM durable con gobierno (presupuesto/timeouts/rework/stop).

    No mantiene estado en memoria: lee el loop persistido en cada operación, por lo que un coordinador
    nuevo (tras reiniciar AIDO) reanuda el loop tal como quedó. Aplica las políticas de parada como parte
    de la lógica determinista, evaluándolas bajo demanda con un ``now`` inyectable.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.repository = ProductLoopRepository(connection)
        self.backlog = BacklogRepository(connection)
        self.discovery = ProductDiscoveryRepository(connection)

    @staticmethod
    def _state_deadline(state: str, timeouts: dict[str, Any] | None, now: str) -> str | None:
        seconds = (timeouts or {}).get(state)
        if seconds is None:
            return None
        return iso_after_seconds(now, float(seconds))

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
        fsm = _empty_fsm()
        fsm["correlationId"] = correlation_id
        fsm["policy"] = {
            "budget": dict(budget or {}),
            "timeouts": dict(timeouts),
            "maxReworkRounds": max_rework_rounds,
            "deadline": deadline,
        }
        fsm["usage"]["stateDeadline"] = self._state_deadline(INITIAL_STATE, timeouts, now)
        loop_context = {**(context or {}), "fsm": fsm}
        with immediate_transaction(self.connection):
            loop = self.repository.create_loop(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "title": title,
                    "state": INITIAL_STATE,
                    "previousState": None,
                    "status": _status_for(INITIAL_STATE),
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
                updated, _transition = self._apply_transition(
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
        fsm = _fsm_of(loop)
        if to_state == REWORK_STATE:
            max_rework = fsm["policy"].get("maxReworkRounds")
            next_rounds = int(fsm["usage"].get("reworkRounds", 0)) + 1
            if max_rework is not None and next_rounds > int(max_rework):
                raise ProductLoopStopConditionError(
                    f"Maximum rework rounds ({max_rework}) reached for product loop {loop_id}."
                )
            fsm["usage"]["reworkRounds"] = next_rounds
        fsm["usage"]["stateDeadline"] = self._state_deadline(to_state, fsm["policy"].get("timeouts"), now)
        if _fsm_patch:
            fsm = _deep_merge_fsm(fsm, _fsm_patch)
        effective_correlation = correlation_id if correlation_id is not None else fsm.get("correlationId")
        next_version = loop["version"] + 1
        context = {**loop["context"], **(context_patch or {})}
        context["fsm"] = fsm
        transition_metadata = {**(metadata or {}), "correlationId": effective_correlation}
        updated = self.repository.update_loop_state(
            loop_id,
            state=to_state,
            previous_state=current,
            status=_status_for(to_state),
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

    def classify_feedback(
        self, *, action: str, target_type: str | None = None, payload: dict[str, Any] | None = None
    ) -> str:
        """Clasifica un comando de feedback en el impacto de producto que debe atenderse."""
        action_key = _normalize_key(action)
        if action_key not in FEEDBACK_ACTIONS:
            raise ProductLoopTransitionError(f"Unknown feedback action: {action}")
        payload = payload or {}
        target_key = _normalize_key(target_type)
        if action_key in {"request_changes", "reopen_story"}:
            return "rework_task"
        if action_key == "reject_decision":
            return "architecture_revision"
        if action_key == "change_scope":
            if isinstance(payload.get("epic"), dict):
                return "new_epic"
            if isinstance(payload.get("story"), dict):
                return "new_story"
            return "brief_revision"
        if action_key == "reprioritize":
            return "brief_revision"
        return TARGET_CLASSIFICATIONS.get(target_key or "loop", "brief_revision")

    def _assert_project_scope(self, record: dict[str, Any], project_id: str, *, label: str) -> None:
        if record["projectId"] != project_id:
            raise ProductLoopTransitionError(f"{label} is not scoped to product loop project {project_id}.")

    def _transition_for_feedback(
        self,
        *,
        loop_id: str,
        to_state: str,
        feedback_id: str,
        action: str,
        classification: str,
        feedback: str,
        actor: str,
        target_type: str,
        target_id: str,
        correlation_id: str | None,
        expected_version: int | None,
        now: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = {
            "feedbackId": feedback_id,
            "feedbackAction": action,
            "feedbackClassification": classification,
            "targetType": target_type,
            "targetId": target_id,
        }
        return self._apply_transition(
            loop_id,
            to_state=to_state,
            reason=feedback,
            actor=actor,
            trigger=action,
            correlation_id=correlation_id,
            expected_version=expected_version,
            metadata=metadata,
            now=now,
        )

    def _apply_feedback_effects(
        self,
        *,
        loop: dict[str, Any],
        feedback_id: str,
        action: str,
        classification: str,
        feedback: str,
        actor: str,
        target_type: str,
        target_id: str,
        payload: dict[str, Any],
        correlation_id: str | None,
        expected_version: int | None,
        now: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        effects: list[dict[str, Any]] = []
        loop_id = loop["id"]
        project_id = loop["projectId"]

        if action == "accept":
            loop, transition = self._transition_for_feedback(
                loop_id=loop_id,
                to_state=DELIVERED_STATE,
                feedback_id=feedback_id,
                action=action,
                classification=classification,
                feedback=feedback,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                correlation_id=correlation_id,
                expected_version=expected_version,
                now=now,
            )
            effects.append(
                {
                    "type": "transition",
                    "transitionId": transition["id"],
                    "fromState": transition["fromState"],
                    "toState": transition["toState"],
                }
            )
            return loop, effects

        if action == "request_changes":
            if target_type != "task" or not target_id:
                raise ProductLoopTransitionError(
                    "Feedback action request_changes requires a traceable target task."
                )
            loop, transition = self._transition_for_feedback(
                loop_id=loop_id,
                to_state=REWORK_STATE,
                feedback_id=feedback_id,
                action=action,
                classification=classification,
                feedback=feedback,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                correlation_id=correlation_id,
                expected_version=expected_version,
                now=now,
            )
            effects.append(
                {
                    "type": "transition",
                    "transitionId": transition["id"],
                    "fromState": transition["fromState"],
                    "toState": transition["toState"],
                }
            )
            task = self.backlog.get_agent_task(target_id)
            self._assert_project_scope(task, project_id, label="Agent task")
            updated = self.backlog.update_agent_task(
                target_id,
                {
                    "status": "todo",
                    "metadata": _append_feedback_id(task.get("metadata"), feedback_id),
                },
            )
            effects.append({"type": "update_agent_task", "id": updated["id"], "status": updated["status"]})
            return loop, effects

        if action == "change_scope":
            if classification == "new_epic":
                epic_payload = dict(payload.get("epic") or {})
                if not str(epic_payload.get("title") or "").strip():
                    raise ProductLoopTransitionError("Feedback action change_scope requires epic.title.")
                metadata = {**dict(epic_payload.get("metadata") or {}), "feedbackId": feedback_id}
                epic = self.backlog.create_epic(
                    {
                        "projectId": project_id,
                        "title": epic_payload["title"],
                        "description": epic_payload.get("description", ""),
                        "status": epic_payload.get("status", "draft"),
                        "priority": epic_payload.get("priority", "medium"),
                        "owner": epic_payload.get("owner", ""),
                        "metadata": metadata,
                    }
                )
                effects.append({"type": "create_epic", "id": epic["id"]})
                return loop, effects
            if classification == "new_story":
                story_payload = dict(payload.get("story") or {})
                epic_id = story_payload.get("epicId") or (target_id if target_type == "epic" else "")
                if not str(epic_id or "").strip():
                    raise ProductLoopTransitionError("Feedback action change_scope requires an epic target.")
                epic = self.backlog.get_epic(str(epic_id))
                self._assert_project_scope(epic, project_id, label="Epic")
                if not str(story_payload.get("title") or "").strip():
                    raise ProductLoopTransitionError("Feedback action change_scope requires story.title.")
                metadata = {**dict(story_payload.get("metadata") or {}), "feedbackId": feedback_id}
                story = self.backlog.create_user_story(
                    {
                        "projectId": project_id,
                        "epicId": epic["id"],
                        "title": story_payload["title"],
                        "asA": story_payload.get("asA", ""),
                        "iWant": story_payload.get("iWant", ""),
                        "soThat": story_payload.get("soThat", ""),
                        "description": story_payload.get("description", ""),
                        "status": story_payload.get("status", "draft"),
                        "priority": story_payload.get("priority", "medium"),
                        "businessValue": story_payload.get("businessValue", "medium"),
                        "storyPoints": story_payload.get("storyPoints"),
                        "owner": story_payload.get("owner", ""),
                        "metadata": metadata,
                    }
                )
                effects.append({"type": "create_user_story", "id": story["id"]})
                return loop, effects
            effects.append(
                {"type": "record_brief_revision", "targetType": target_type, "targetId": target_id}
            )
            return loop, effects

        if action == "reprioritize":
            priority = str(payload.get("priority") or "").strip()
            if not priority or target_type not in {"epic", "story", "task"} or not target_id:
                raise ProductLoopTransitionError("Feedback action reprioritize requires priority and target.")
            if target_type == "epic":
                epic = self.backlog.get_epic(target_id)
                self._assert_project_scope(epic, project_id, label="Epic")
                updated = self.backlog.update_epic(
                    target_id,
                    {
                        "priority": priority,
                        "metadata": _append_feedback_id(epic.get("metadata"), feedback_id),
                    },
                )
            elif target_type == "story":
                story = self.backlog.get_user_story(target_id)
                self._assert_project_scope(story, project_id, label="User story")
                updated = self.backlog.update_user_story(
                    target_id,
                    {
                        "priority": priority,
                        "metadata": _append_feedback_id(story.get("metadata"), feedback_id),
                    },
                )
            else:
                task = self.backlog.get_agent_task(target_id)
                self._assert_project_scope(task, project_id, label="Agent task")
                updated = self.backlog.update_agent_task(
                    target_id,
                    {
                        "priority": priority,
                        "metadata": _append_feedback_id(task.get("metadata"), feedback_id),
                    },
                )
            effects.append(
                {
                    "type": "update_priority",
                    "targetType": target_type,
                    "id": updated["id"],
                    "priority": priority,
                }
            )
            return loop, effects

        if action == "reject_decision":
            if target_type != "decision" or not target_id:
                raise ProductLoopTransitionError(
                    "Feedback action reject_decision requires a traceable decision target."
                )
            decision = self.discovery.get_product_decision(target_id)
            self._assert_project_scope(decision, project_id, label="Product decision")
            updated = self.discovery.update_product_decision(
                target_id,
                {
                    "status": "rejected",
                    "decidedBy": actor,
                    "decidedAt": utc_now(),
                    "metadata": _append_feedback_id(decision.get("metadata"), feedback_id),
                },
            )
            effects.append({"type": "reject_decision", "id": updated["id"], "status": updated["status"]})
            return loop, effects

        if action == "reopen_story":
            if target_type != "story" or not target_id:
                raise ProductLoopTransitionError(
                    "Feedback action reopen_story requires a traceable story target."
                )
            story = self.backlog.get_user_story(target_id)
            self._assert_project_scope(story, project_id, label="User story")
            updated = self.backlog.update_user_story(
                target_id,
                {
                    "status": "reopened",
                    "metadata": _append_feedback_id(story.get("metadata"), feedback_id),
                },
            )
            effects.append({"type": "reopen_story", "id": updated["id"], "status": updated["status"]})
            return loop, effects

        if action == "pause_loop":
            loop, transition = self._transition_for_feedback(
                loop_id=loop_id,
                to_state=BLOCKED_STATE,
                feedback_id=feedback_id,
                action=action,
                classification=classification,
                feedback=feedback,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                correlation_id=correlation_id,
                expected_version=expected_version,
                now=now,
            )
            effects.append(
                {
                    "type": "transition",
                    "transitionId": transition["id"],
                    "fromState": transition["fromState"],
                    "toState": transition["toState"],
                }
            )
            return loop, effects

        if action == "cancel_loop":
            loop, transition = self._transition_for_feedback(
                loop_id=loop_id,
                to_state=CANCELLED_STATE,
                feedback_id=feedback_id,
                action=action,
                classification=classification,
                feedback=feedback,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                correlation_id=correlation_id,
                expected_version=expected_version,
                now=now,
            )
            effects.append(
                {
                    "type": "transition",
                    "transitionId": transition["id"],
                    "fromState": transition["fromState"],
                    "toState": transition["toState"],
                }
            )
            return loop, effects

        raise ProductLoopTransitionError(f"Unknown feedback action: {action}")

    def apply_feedback(
        self,
        loop_id: str,
        *,
        action: str,
        feedback: str,
        actor: str = "operator",
        target_type: str | None = None,
        target_id: str | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        expected_version: int | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Clasifica y aplica una acción de feedback, dejando feedback, efectos y transición enlazados."""
        action_key = _normalize_key(action)
        if action_key not in FEEDBACK_ACTIONS:
            raise ProductLoopTransitionError(f"Unknown feedback action: {action}")
        feedback_text = str(redact_secrets(str(feedback or "").strip()))
        if not feedback_text:
            raise ProductLoopTransitionError("Feedback text is required.")
        payload = dict(payload or {})
        target_key = _normalize_key(target_type) or "loop"
        effective_target_id = str(target_id or "").strip()
        if target_key == "loop" and not effective_target_id:
            effective_target_id = loop_id
        if action_key in TARGET_REQUIRED_ACTIONS and not effective_target_id:
            raise ProductLoopTransitionError(f"Feedback action {action_key} requires a traceable target.")
        classification = self.classify_feedback(action=action_key, target_type=target_key, payload=payload)
        if classification not in FEEDBACK_CLASSIFICATIONS:
            raise ProductLoopTransitionError(f"Unknown feedback classification: {classification}")

        try:
            with immediate_transaction(self.connection):
                loop = self.repository.get_loop(loop_id)
                if expected_version is not None and int(expected_version) != loop["version"]:
                    raise ProductLoopTransitionError(
                        f"Product loop version mismatch: expected {expected_version}, found {loop['version']}."
                    )
                feedback_record = self.repository.create_feedback(
                    {
                        "loopId": loop_id,
                        "projectId": loop["projectId"],
                        "action": action_key,
                        "classification": classification,
                        "feedback": feedback_text,
                        "actor": actor,
                        "targetType": target_key,
                        "targetId": effective_target_id,
                        "status": "recorded",
                        "effects": [],
                        "metadata": {"payload": payload, "correlationId": correlation_id},
                    }
                )
                updated_loop, effects = self._apply_feedback_effects(
                    loop=loop,
                    feedback_id=feedback_record["id"],
                    action=action_key,
                    classification=classification,
                    feedback=feedback_text,
                    actor=actor,
                    target_type=target_key,
                    target_id=effective_target_id,
                    payload=payload,
                    correlation_id=correlation_id,
                    expected_version=expected_version,
                    now=now,
                )
                feedback_record = self.repository.update_feedback_effects(
                    feedback_record["id"],
                    effects=effects,
                    status="applied",
                )
        except sqlite3.IntegrityError as error:
            raise ProductLoopTransitionError(
                f"Concurrent product loop feedback application detected for product loop {loop_id}."
            ) from error
        return {
            "loop": updated_loop,
            "feedback": feedback_record,
            "resumable": not is_terminal(updated_loop["state"]),
            "allowedNextStates": sorted(ALLOWED_TRANSITIONS[updated_loop["state"]]),
            "transitions": self.repository.list_transitions(loop_id),
        }

    def list_feedback(
        self, *, loop_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista los feedback commands trazables por loop o proyecto."""
        return self.repository.list_feedback(loop_id=loop_id, project_id=project_id)

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
        return self.transition(
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
        return self.transition(
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
        return self.transition(
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
            fsm = _fsm_of(loop)
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
            updated = self.transition(
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
