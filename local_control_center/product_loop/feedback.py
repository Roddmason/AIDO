"""Feedback del operador sobre un product loop: clasificación, transición y efectos durables.

Colaborador de ``ProductLoopCoordinator`` extraído de ``coordinator.py``. Clasifica el comando de feedback
en su impacto de producto, decide la transición del loop y aplica sus efectos sobre backlog, decisiones,
aprobaciones de entrega y continuaciones, todo en la transacción del coordinator. Las operaciones del loop
y de entrega se resuelven a través del coordinator para conservar el parcheo por clase o por instancia.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .fsm import (
    ALLOWED_TRANSITIONS,
    AWAITING_FEEDBACK_STATE,
    BLOCKED_STATE,
    CANCELLED_STATE,
    DELIVERED_STATE,
    REWORK_STATE,
    ProductLoopTransitionError,
    is_terminal,
)
from .models import FEEDBACK_ACTION_VALUES, FEEDBACK_CLASSIFICATION_VALUES

if TYPE_CHECKING:
    from .coordinator import ProductLoopCoordinator
    from .repository import ProductLoopRepository

__all__ = ["FeedbackProcessor"]

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


def _normalize_key(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _append_feedback_id(metadata: dict[str, Any] | None, feedback_id: str) -> dict[str, Any]:
    result = dict(metadata or {})
    feedback_ids = [str(item) for item in result.get("feedbackIds", []) if str(item).strip()]
    if feedback_id not in feedback_ids:
        feedback_ids.append(feedback_id)
    result["feedbackIds"] = feedback_ids
    return result


class FeedbackProcessor:
    """Aplica el feedback del operador a un product loop y deja su rastro trazable."""

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
        if action_key == "continue":
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
        return self._coordinator._apply_transition(
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
            approval_effect = self._coordinator._resolve_delivery_approval_action(
                loop=loop,
                decision=action,
                reason=feedback,
                actor=actor,
            )
            loop = self._coordinator._record_delivery_approval_decision(loop, approval_effect)
            loop, landing_effect = self._coordinator._queue_delivery_landing(loop, feedback_id=feedback_id)
            thread_effect = self._coordinator._sync_delivery_feedback_thread_state(
                loop=loop,
                decision=action,
                reason=feedback,
                actor=actor,
            )
            effects.append(
                {
                    "type": "transition",
                    "transitionId": transition["id"],
                    "fromState": transition["fromState"],
                    "toState": transition["toState"],
                }
            )
            if approval_effect:
                effects.append(approval_effect)
            if landing_effect:
                effects.append(landing_effect)
            if thread_effect:
                effects.append(thread_effect)
            return loop, effects

        if action == "request_changes":
            if target_type == "loop":
                loop, transition = self._transition_for_feedback(
                    loop_id=loop_id,
                    to_state=AWAITING_FEEDBACK_STATE,
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
                approval_effect = self._coordinator._resolve_delivery_approval_action(
                    loop=loop,
                    decision=action,
                    reason=feedback,
                    actor=actor,
                )
                loop = self._coordinator._record_delivery_approval_decision(loop, approval_effect)
                thread_effect = self._coordinator._sync_delivery_feedback_thread_state(
                    loop=loop,
                    decision=action,
                    reason=feedback,
                    actor=actor,
                )
                effects.append(
                    {
                        "type": "transition",
                        "transitionId": transition["id"],
                        "fromState": transition["fromState"],
                        "toState": transition["toState"],
                    }
                )
                if approval_effect:
                    effects.append(approval_effect)
                if thread_effect:
                    effects.append(thread_effect)
                return loop, effects
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
            task = self._coordinator.backlog.get_agent_task(target_id)
            self._assert_project_scope(task, project_id, label="Agent task")
            updated = self._coordinator.backlog.update_agent_task(
                target_id,
                {
                    "status": "todo",
                    "metadata": _append_feedback_id(task.get("metadata"), feedback_id),
                },
            )
            approval_effect = self._coordinator._resolve_delivery_approval_action(
                loop=loop,
                decision=action,
                reason=feedback,
                actor=actor,
            )
            loop = self._coordinator._record_delivery_approval_decision(loop, approval_effect)
            thread_effect = self._coordinator._sync_delivery_feedback_thread_state(
                loop=loop,
                decision=action,
                reason=feedback,
                actor=actor,
            )
            effects.append({"type": "update_agent_task", "id": updated["id"], "status": updated["status"]})
            if approval_effect:
                effects.append(approval_effect)
            if thread_effect:
                effects.append(thread_effect)
            return loop, effects

        if action == "continue":
            if loop["state"] != AWAITING_FEEDBACK_STATE:
                raise ProductLoopTransitionError(
                    "Feedback action continue requires a Product Loop awaiting feedback."
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
            loop, continuation_effect = self._coordinator._queue_delivery_feedback_continuation(
                loop=loop,
                feedback_id=feedback_id,
                feedback=feedback,
                actor=actor,
            )
            effects.append(
                {
                    "type": "transition",
                    "transitionId": transition["id"],
                    "fromState": transition["fromState"],
                    "toState": transition["toState"],
                }
            )
            effects.append(continuation_effect)
            return loop, effects

        if action == "change_scope":
            if classification == "new_epic":
                epic_payload = dict(payload.get("epic") or {})
                if not str(epic_payload.get("title") or "").strip():
                    raise ProductLoopTransitionError("Feedback action change_scope requires epic.title.")
                metadata = {**dict(epic_payload.get("metadata") or {}), "feedbackId": feedback_id}
                epic = self._coordinator.backlog.create_epic(
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
                epic = self._coordinator.backlog.get_epic(str(epic_id))
                self._assert_project_scope(epic, project_id, label="Epic")
                if not str(story_payload.get("title") or "").strip():
                    raise ProductLoopTransitionError("Feedback action change_scope requires story.title.")
                metadata = {**dict(story_payload.get("metadata") or {}), "feedbackId": feedback_id}
                acceptance_criteria = [
                    str(item).strip()
                    for item in story_payload.get("acceptanceCriteria") or []
                    if str(item).strip()
                ]
                if not acceptance_criteria:
                    raise ProductLoopTransitionError(
                        "Feedback action change_scope requires story.acceptanceCriteria."
                    )
                story = self._coordinator.backlog.create_user_story(
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
                        "acceptanceCriteria": acceptance_criteria,
                        "acceptanceCriteriaMetadata": {"feedbackId": feedback_id},
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
                epic = self._coordinator.backlog.get_epic(target_id)
                self._assert_project_scope(epic, project_id, label="Epic")
                updated = self._coordinator.backlog.update_epic(
                    target_id,
                    {
                        "priority": priority,
                        "metadata": _append_feedback_id(epic.get("metadata"), feedback_id),
                    },
                )
            elif target_type == "story":
                story = self._coordinator.backlog.get_user_story(target_id)
                self._assert_project_scope(story, project_id, label="User story")
                updated = self._coordinator.backlog.update_user_story(
                    target_id,
                    {
                        "priority": priority,
                        "metadata": _append_feedback_id(story.get("metadata"), feedback_id),
                    },
                )
            else:
                task = self._coordinator.backlog.get_agent_task(target_id)
                self._assert_project_scope(task, project_id, label="Agent task")
                updated = self._coordinator.backlog.update_agent_task(
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
            decision = self._coordinator.discovery.get_product_decision(target_id)
            self._assert_project_scope(decision, project_id, label="Product decision")
            updated = self._coordinator.discovery.update_product_decision(
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
            story = self._coordinator.backlog.get_user_story(target_id)
            self._assert_project_scope(story, project_id, label="User story")
            updated = self._coordinator.backlog.update_user_story(
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
        if action_key == "request_changes" and not effective_target_id:
            raise ProductLoopTransitionError("Feedback action request_changes requires a traceable target.")
        if target_key == "loop" and not effective_target_id:
            effective_target_id = loop_id
        if action_key in TARGET_REQUIRED_ACTIONS and not effective_target_id:
            raise ProductLoopTransitionError(f"Feedback action {action_key} requires a traceable target.")
        classification = self._coordinator.classify_feedback(
            action=action_key, target_type=target_key, payload=payload
        )
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
