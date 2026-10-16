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
import uuid
from collections.abc import Callable
from contextlib import suppress
from functools import cached_property
from pathlib import Path
from typing import Any

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.architect_agent import ArchitectAgentRunner
from local_control_center.agents.devops_agent import DevOpsAgentRunner
from local_control_center.agents.product_owner_agent import (
    ProductOwnerAgent,
    ProductOwnerOutputValidationError,
    persist_product_owner_backlog,
)
from local_control_center.agents.product_owner_agent_contract import (
    PRODUCT_OWNER_AGENT_ID,
)
from local_control_center.agents.provider_accounts import disabled_provider_ids
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.backlog.story_spec import build_story_spec, render_story_spec_prompt
from local_control_center.backlog.technical_lead_planner import TechnicalLeadPlanner
from local_control_center.decision_engine.config import resolve_config as resolve_config
from local_control_center.decision_engine.observers import (
    observe_resource_decision as observe_resource_decision,
)
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.intent_classifier import IntentClassificationInput, IntentClassifier
from local_control_center.project_constitution.prompt import render_constitution_prompt
from local_control_center.project_constitution.repository import ProjectConstitutionRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.quality.plans import iteration_scripts
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_team.configuration import (
    assigned_runtime,
    discarded_role_runtime,
    runtime_team_of,
)
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import iso_after_seconds, utc_now
from local_control_center.team_scheduler.scheduler import schedule_team
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import SIMILARITY_ACTIONS, ThreadMemoryService
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff as capture_git_diff

from .feedback import FeedbackProcessor
from .fsm import (
    ALLOWED_TRANSITIONS,
    AWAITING_FEEDBACK_STATE,
    BLOCKED_STATE,
    CANCELLED_STATE,
    RESUMABLE_STATES,
    REWORK_STATE,
    TERMINAL_STATES,
    ProductLoopStopConditionError,
    ProductLoopTransitionError,
    is_terminal,
)
from .fsm import (
    DEFAULT_AUTO_REWORK_ROUNDS as DEFAULT_AUTO_REWORK_ROUNDS,
)
from .fsm import (
    INITIAL_STATE as INITIAL_STATE,
)
from .fsm import (
    PRODUCT_LOOP_STATES as PRODUCT_LOOP_STATES,
)
from .fsm import (
    allowed_next_states as allowed_next_states,
)
from .loop_fsm import ProductLoopFsm
from .phases import approval as approval_phase
from .phases import delivery_landing as delivery_landing_phase
from .phases import discovery as discovery_phase
from .phases import environment as environment_phase
from .phases import execution as execution_phase
from .phases import failover as failover_phase
from .phases import intake as intake_phase
from .phases import qa_gate as qa_gate_phase
from .phases import resource_learning as resource_learning_phase
from .phases import resource_selection as resource_selection_phase
from .phases import security as security_phase
from .phases import story_loop as story_loop_phase
from .phases import team_planning as team_planning_phase
from .phases.analysis import analyze_run_consistency
from .phases.plan import PLANNED, SKIPPED_LOW_RISK, plan_phase_decision, technical_plan_view
from .repository import ProductLoopRepository, stable_task_suffix
from .resource_approval_markers import (
    approved_resource_selection_for,
    public_resource_decision,
    resource_context_tokens_estimate,
    resource_decision_has_pending_approval,
    resource_privacy_level,
    resource_required_capabilities,
    resource_role_matches,
    resource_selection_matches,
    same_resource_selection,
    select_walking_providers,
    source_resource_approval_loop_id,
    unscheduled_agent_task_roles,
)
from .review_views import (
    changed_files_from_diff,
    compact_runtime_result,
    diff_ref_from_review,
    diff_summary_from_review,
    review_from_diff,
    review_from_runtime,
    runtime_changed_files,
)
from .run_context import UserMessageRun
from .run_instructions import (
    EARLIER_CHANGE_REQUEST_HEADER as EARLIER_CHANGE_REQUEST_HEADER,
)
from .run_instructions import (
    INSTRUCTION_PROMPT_LIMIT_CHARS as INSTRUCTION_PROMPT_LIMIT_CHARS,
)
from .run_instructions import (
    bounded_instruction,
)
from .run_instructions import (
    continuation_run_message as continuation_run_message,
)
from .runtime_metrics import (
    runtime_actual_cost_usd,
    runtime_bool_metric,
    runtime_latency_ms,
    runtime_provider_usage,
    runtime_quality_score_metric,
    runtime_resource_usage_entries,
)
from .spec_artifacts import write_spec_artifacts
from .team_shape import failed_team_schedule, team_mode, team_risk, team_scope

_RESUMABLE_STATES = RESUMABLE_STATES
_UserMessageRun = UserMessageRun
_changed_files_from_diff = changed_files_from_diff
_runtime_changed_files = runtime_changed_files
_review_from_diff = review_from_diff
_review_from_runtime = review_from_runtime
_compact_runtime_result = compact_runtime_result
_diff_ref_from_review = diff_ref_from_review
_diff_summary_from_review = diff_summary_from_review
_bounded_instruction = bounded_instruction

REWORK_FEEDBACK_COMMAND_LIMIT = 8
"""Tope de comandos QA fallidos detallados en el feedback de rework; el resto se reporta como conteo."""
_OPERATOR_WAIT_STATES = {"awaiting_user", "awaiting_approval", AWAITING_FEEDBACK_STATE, BLOCKED_STATE}
"""Estados donde el loop entrego control al operador y espera su accion: nunca se supersedan como runs
interrumpidos, aunque su marcador runActive haya quedado colgado (defensa del re-entry durable frente a un
_clear_run_active best-effort fallido).
"""
_ABORT_EXEMPT_STATES = {BLOCKED_STATE, CANCELLED_STATE}
"""Landing states of an abort itself: checking for cancellation again here would recurse forever."""
RUN_RESULT_STATUSES = (
    "awaiting_approval",
    "awaiting_user",
    "blocked",
    "brief_ready",
    "cancelled",
    "completed",
    "plan_ready",
    "reworking",
)
"""Contrato de salida de _run_user_message: todo status que un run puede retornar. El worker y los gates de
taxonomía (test_product_loop_result_taxonomy.py) enumeran contra esta tupla, así que un status nuevo
obliga a decidir su ruteo (hilo/evento/veredicto) en el mismo commit.
"""
RUN_EVIDENCE_PASSED_STATUSES = frozenset(
    {
        "completed",
        "awaiting_approval",
        "reworking",
        "awaiting_user",
        "needs_input",
        "brief_ready",
        "backlog_ready",
        "plan_ready",
    }
)
"""Statuses de etapa que representan un cierre sano: producen veredicto de evidencia "passed". Cualquier
status fuera de este set cae al lado bloqueado (severidad high) a propósito.
"""
THREAD_RESEARCH_JOB_KIND = "thread.research.run"
HIGH_IMPACT_RESEARCH_VALUES = {"high", "critical"}
TECHNICAL_DECISION_CATEGORIES = {
    "architecture",
    "database",
    "data",
    "devops",
    "infra",
    "infrastructure",
    "migration",
    "security",
    "technical",
}


class _ProductLoopCancelled(RuntimeError):
    """Control de flujo interno: el operador canceló el run y el loop debe abortar en el límite de etapa.

    Nunca escapa de ``run_user_message``, que la traduce al estado terminal ``cancelled``.
    """

    def __init__(self, loop_id: str, thread_id: str | None) -> None:
        super().__init__(f"Product Loop {loop_id} was cancelled by the operator.")
        self.loop_id = loop_id
        self.thread_id = thread_id


def _thread_agent_role_for_state(state: str) -> str:
    if state == "executing":
        return "developer"
    if state == "qa_running":
        return "qa_reviewer"
    if state == "security_running":
        return "security_reviewer"
    return "aido_lead"


class ProductLoopCoordinator:
    """Coordina un product loop como FSM durable con gobierno (presupuesto/timeouts/rework/stop).

    No mantiene estado en memoria: lee el loop persistido en cada operación, por lo que un coordinador
    nuevo (tras reiniciar AIDO) reanuda el loop tal como quedó. Aplica las políticas de parada como parte
    de la lógica determinista, evaluándolas bajo demanda con un ``now`` inyectable.
    """

    _runtime_provider_usage = staticmethod(runtime_provider_usage)
    _runtime_actual_cost_usd = staticmethod(runtime_actual_cost_usd)
    _runtime_latency_ms = staticmethod(runtime_latency_ms)
    _runtime_bool_metric = staticmethod(runtime_bool_metric)
    _runtime_quality_score_metric = staticmethod(runtime_quality_score_metric)
    _runtime_resource_usage_entries = staticmethod(runtime_resource_usage_entries)
    _public_resource_decision = staticmethod(public_resource_decision)
    _approved_resource_selection_for = staticmethod(approved_resource_selection_for)
    _same_resource_selection = staticmethod(same_resource_selection)
    _source_resource_approval_loop_id = staticmethod(source_resource_approval_loop_id)
    _resource_decision_has_pending_approval = staticmethod(resource_decision_has_pending_approval)
    _resource_required_capabilities = staticmethod(resource_required_capabilities)
    _resource_context_tokens_estimate = staticmethod(resource_context_tokens_estimate)
    _resource_privacy_level = staticmethod(resource_privacy_level)
    _resource_role_matches = staticmethod(resource_role_matches)
    _resource_selection_matches = staticmethod(resource_selection_matches)
    _select_walking_providers = staticmethod(select_walking_providers)
    _unscheduled_agent_task_roles = staticmethod(unscheduled_agent_task_roles)
    _team_mode = staticmethod(team_mode)
    _team_risk = staticmethod(team_risk)
    _team_scope = staticmethod(team_scope)
    _failed_team_schedule = staticmethod(failed_team_schedule)

    def __init__(self, connection: sqlite3.Connection, *, root: str | Path | None = None):
        self.connection = connection
        self.repository = ProductLoopRepository(connection)
        self.backlog = BacklogRepository(connection)
        self.discovery = ProductDiscoveryRepository(connection)
        self.root = Path(root).resolve(strict=False) if root is not None else None
        self.agents = AgentsRepository(connection)
        self.routing_profiles = RoutingProfileStore(connection)
        self.evidence = EvidenceRepository(connection)
        self.events = EventBus(connection)
        self.jobs = JobsRepository(connection)
        self._should_abort: Callable[[], bool] | None = None

    @cached_property
    def loop_fsm(self) -> ProductLoopFsm:
        """FSM durable del loop (alta, transiciones, bloqueo, consumo y reanudación)."""
        return ProductLoopFsm(self)

    @cached_property
    def feedback_processor(self) -> FeedbackProcessor:
        """Procesador de feedback del operador (clasificación, transición y efectos)."""
        return FeedbackProcessor(self)

    @staticmethod
    def _state_deadline(state: str, timeouts: dict[str, Any] | None, now: str) -> str | None:
        seconds = (timeouts or {}).get(state)
        if seconds is None:
            return None
        return iso_after_seconds(now, float(seconds))

    def _write_context_entries(self, loop: dict[str, Any], entries: dict[str, Any]) -> dict[str, Any]:
        """Escribe ``entries`` sobre el contexto del loop tal como está persistido AHORA.

        Los llamadores arman ``entries`` desde un ``loop`` leído antes de una operación larga; mezclarlo
        con ese ``loop["context"]`` obsoleto pisaría lo que otro escritor (transición, ``record_usage``,
        aprobaciones) confirmó mientras tanto. Aquí se re-lee dentro de la misma transacción que escribe;
        si el loop ya no existe se usa la instantánea del llamador y ``update_loop_context`` decide el error.
        """

        def write() -> dict[str, Any]:
            try:
                current = self.repository.get_loop(loop["id"])
            except KeyError:
                current = loop
            return self.repository.update_loop_context(loop["id"], context={**current["context"], **entries})

        if self.connection.in_transaction:
            return write()
        with immediate_transaction(self.connection):
            return write()

    def _durable_run_context(self, loop: dict[str, Any]) -> dict[str, Any]:
        return dict((loop.get("context") or {}).get("durableRun") or {})

    @staticmethod
    def _approved_resource_selections_from_durable(durable: dict[str, Any]) -> list[dict[str, Any]]:
        resource_approval = (
            durable.get("resourceApproval") if isinstance(durable.get("resourceApproval"), dict) else {}
        )
        approvals = resource_approval.get("approvedResourceSelections")
        if resource_approval.get("status") != "approved" or not isinstance(approvals, list):
            return []
        return [approval for approval in approvals if isinstance(approval, dict)]

    def _durable_run_patch(
        self,
        loop: dict[str, Any],
        updates: dict[str, Any],
        *,
        evidence_package_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        original = self._durable_run_context(loop)
        clean_updates = redact_secrets(updates)
        resolution = original.get("researchResolution") or {}
        risk_review = original.get("runtimeRiskReview") or {}
        if (
            (
                isinstance(resolution, dict)
                and resolution.get("status") == "consumed"
                and resolution.get("loopId") == loop["id"]
            )
            or (
                isinstance(risk_review, dict)
                and risk_review.get("status") in {"consuming", "consumed"}
                and risk_review.get("loopId") == loop["id"]
            )
        ) and "requestMeta" in clean_updates:
            clean_updates["effectiveRequestMeta"] = clean_updates.pop("requestMeta")
        durable = {**original, **clean_updates}
        ids = [str(item) for item in durable.get("evidencePackageIds", []) if str(item).strip()]
        for evidence_id in evidence_package_ids or []:
            if evidence_id and evidence_id not in ids:
                ids.append(evidence_id)
        durable["evidencePackageIds"] = ids
        durable["updatedAt"] = utc_now()
        return {"durableRun": durable}

    def _resolve_delivery_approval_action(
        self,
        *,
        loop: dict[str, Any],
        decision: str,
        reason: str,
        actor: str,
    ) -> dict[str, Any] | None:
        return delivery_landing_phase.resolve_delivery_approval_action(
            self, loop=loop, decision=decision, reason=reason, actor=actor
        )

    def _record_delivery_approval_decision(
        self, loop: dict[str, Any], effect: dict[str, Any] | None
    ) -> dict[str, Any]:
        return delivery_landing_phase.record_delivery_approval_decision(self, loop, effect)

    def _queue_delivery_landing(
        self, loop: dict[str, Any], *, feedback_id: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return delivery_landing_phase.queue_delivery_landing(self, loop, feedback_id=feedback_id)

    def _land_delivered_work(self, loop: dict[str, Any]) -> dict[str, Any]:
        return delivery_landing_phase.land_delivered_work(self, loop)

    def land_queued_delivery(self, loop_id: str) -> dict[str, Any]:
        """Aterriza una entrega aprobada desde su job; un aterrizaje ya resuelto no se repite."""
        return delivery_landing_phase.land_queued_delivery(self, loop_id)

    def _report_delivery_landing(self, loop: dict[str, Any], landing: dict[str, Any]) -> None:
        return delivery_landing_phase.report_delivery_landing(self, loop, landing)

    def _queue_delivery_feedback_continuation(
        self,
        *,
        loop: dict[str, Any],
        feedback_id: str,
        feedback: str,
        actor: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return delivery_landing_phase.queue_delivery_feedback_continuation(
            self, loop=loop, feedback_id=feedback_id, feedback=feedback, actor=actor
        )

    def _sync_delivery_feedback_thread_state(
        self,
        *,
        loop: dict[str, Any],
        decision: str,
        reason: str,
        actor: str,
    ) -> dict[str, Any] | None:
        return delivery_landing_phase.sync_delivery_feedback_thread_state(
            self, loop=loop, decision=decision, reason=reason, actor=actor
        )

    def _record_loop_event(
        self,
        *,
        project_id: str,
        event_type: str,
        loop_id: str,
        payload: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> None:
        loop_event_payload = {"loopId": loop_id, **redact_secrets(payload or {})}
        try:
            self.events.record_event(
                project_id=project_id,
                event_type=event_type,
                payload=loop_event_payload,
            )
        except Exception as error:
            if thread_id:
                self._record_thread_event(
                    thread_id=thread_id,
                    event_type="loop_event_failed",
                    payload={
                        "loopId": loop_id,
                        "loopEventType": event_type,
                        "reason": str(redact_secrets(str(error))),
                    },
                    agent_role="aido_lead",
                )
        if thread_id:
            self._record_thread_event(
                thread_id=thread_id,
                event_type=event_type.replace("product_loop.", ""),
                payload={"loopId": loop_id, **redact_secrets(payload or {})},
                agent_role="aido_lead",
            )

    def _record_thread_event(
        self,
        *,
        thread_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
        agent_role: str | None = None,
    ) -> None:
        if not thread_id:
            return
        threads = ThreadsRepository(self.connection)
        try:
            threads.record_event(
                thread_id=thread_id,
                type=event_type,
                agent_role=agent_role,
                payload=payload or {},
            )
        except Exception as error:
            with suppress(Exception):
                thread = threads.get_thread(thread_id)
                self.events.record_event(
                    project_id=thread["projectId"],
                    event_type="product_loop.thread_event_failed",
                    payload=redact_secrets(
                        {
                            "threadId": thread_id,
                            "threadEventType": event_type,
                            "agentRole": agent_role,
                            "reason": str(error),
                        }
                    ),
                )

    def _record_local_model_switches(
        self,
        *,
        project_id: str,
        loop_id: str,
        thread_id: str | None,
        role: str,
        decision: dict[str, Any],
    ) -> None:
        """Registra ``local_model_switch`` cuando el recurso elegido exige cambiar el modelo cargado.

        Queda siempre en la auditoría de la ejecución (``product_loop.local_model_switch``) y, si hay hilo,
        en su línea de tiempo: así el desalojo del modelo de otra sesión es visible.
        """
        selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
        policy_result = decision.get("policyResult") if isinstance(decision.get("policyResult"), dict) else {}
        for entry in policy_result.get("localModelSelections") or []:
            if not (
                isinstance(entry, dict)
                and entry.get("requiresSwitch")
                and entry.get("runtimeId") == selected.get("providerId")
                and entry.get("model") == selected.get("model")
            ):
                continue
            self._record_loop_event(
                project_id=project_id,
                event_type="product_loop.local_model_switch",
                loop_id=loop_id,
                payload={
                    "runtimeId": entry["runtimeId"],
                    "fromModel": entry.get("fromModel"),
                    "toModel": entry["model"],
                    "role": role,
                    "reason": str(entry.get("reason") or ""),
                },
                thread_id=thread_id,
            )

    def _set_thread_status_best_effort(
        self,
        *,
        thread_id: str | None,
        status: str,
        reason: str | None = None,
    ) -> None:
        if not thread_id:
            return
        threads = ThreadsRepository(self.connection)
        try:
            threads.set_status(thread_id, status)
        except Exception as error:
            with suppress(Exception):
                thread = threads.get_thread(thread_id)
                self.events.record_event(
                    project_id=thread["projectId"],
                    event_type="product_loop.thread_status_failed",
                    payload=redact_secrets(
                        {
                            "threadId": thread_id,
                            "targetStatus": status,
                            "reason": reason,
                            "failureReason": str(error),
                        }
                    ),
                )

    def _create_blocker_remediations_best_effort(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        loop_id: str | None,
        stage: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        service = BlockerRemediationService(self.connection, root=self.root)
        try:
            return service.create_for_blocked_run(
                project_id=project_id,
                thread_id=thread_id,
                loop_id=loop_id,
                stage=stage,
                reason=reason,
                details=details or {},
            )
        except Exception as error:
            fallback_actions: list[dict[str, Any]] = []
            clean_details = redact_secrets(details or {})
            with suppress(Exception):
                blocker_type = service._blocker_type(stage=stage, reason=reason, details=clean_details)
                fallback_actions.append(
                    RemediationActionsRepository(self.connection).create_action(
                        project_id=project_id,
                        thread_id=thread_id,
                        loop_id=loop_id,
                        stage=stage,
                        blocker_type=blocker_type,
                        title="Retry loop",
                        description="Retry after remediation actions can be generated normally.",
                        action_type="retry_loop",
                        payload={
                            "projectId": project_id,
                            "threadId": thread_id,
                            "loopId": loop_id,
                            "stage": stage,
                            "blockerType": blocker_type,
                            "reason": reason,
                            "details": clean_details,
                            "remediationFailureReason": str(redact_secrets(str(error))),
                        },
                    )
                )
            self._record_loop_event(
                project_id=project_id,
                event_type="product_loop.remediation_creation_failed",
                loop_id=str(loop_id or ""),
                payload={
                    "stage": stage,
                    "reason": reason,
                    "failureReason": str(redact_secrets(str(error))),
                    "fallbackActionIds": [action["id"] for action in fallback_actions],
                },
                thread_id=thread_id,
            )
            return fallback_actions

    def _transition_run_state(
        self,
        loop: dict[str, Any],
        *,
        to_state: str,
        reason: str,
        trigger: str,
        actor: str,
        context_patch: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        thread_id: str | None = None,
        fsm_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Transiciona el loop registrando evento de loop y de hilo; punto único de cancelación.

        ``fsm_patch`` se mezcla sobre ``context['fsm']`` en la misma transacción (lo usa la arista
        ``next_story`` para reiniciar ``reworkRounds`` por historia).
        """
        if to_state not in _ABORT_EXEMPT_STATES and self._should_abort is not None and self._should_abort():
            raise _ProductLoopCancelled(loop["id"], thread_id)
        updated = self.transition(
            loop["id"],
            to_state=to_state,
            reason=reason,
            actor=actor,
            trigger=trigger,
            context_patch=context_patch,
            metadata=metadata,
            _fsm_patch=fsm_patch,
        )
        self._record_loop_event(
            project_id=updated["projectId"],
            event_type="product_loop.state_changed",
            loop_id=updated["id"],
            payload={
                "fromState": loop["state"],
                "toState": to_state,
                "reason": reason,
                "trigger": trigger,
            },
            thread_id=thread_id,
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type=to_state,
            agent_role=_thread_agent_role_for_state(to_state),
            payload={
                "loopId": updated["id"],
                "fromState": loop["state"],
                "toState": to_state,
                "reason": reason,
                "trigger": trigger,
                "status": to_state,
            },
        )
        return updated

    def _record_run_evidence(
        self,
        *,
        project_id: str,
        loop_id: str,
        stage: str,
        status: str,
        reason: str,
        details: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        diff_refs: list[Any] | None = None,
        diff_summary: dict[str, Any] | None = None,
        test_results: list[Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        policy_decisions: list[dict[str, Any]] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        verdict = "passed" if status in RUN_EVIDENCE_PASSED_STATUSES else "blocked"
        severity = "low" if verdict == "passed" else "high"
        gate_result = {
            "command": f"product_loop.{stage}",
            "status": verdict,
            "metadata": redact_secrets(details or {}),
        }
        return self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=None,
            workspace_id=workspace_id,
            agent_id="product_loop_coordinator",
            task_id=f"product_loop.{stage}",
            test_plan="ProductLoopCoordinator durable gate evidence.",
            acceptance_checklist=[
                "User message produced a concrete loop outcome.",
                "State transition is persisted.",
                "Evidence package captures the gate result.",
            ],
            test_results=[gate_result, *(test_results or [])],
            logs=[redact_secrets({"stage": stage, "status": status, "reason": reason, "loopId": loop_id})],
            diff_refs=diff_refs,
            artifact_ids=artifact_ids,
            risk_notes=[
                {
                    "severity": severity,
                    "description": reason,
                    "mitigation": "Address the blocked gate and rerun the Product Loop message.",
                }
            ],
            diff_summary=diff_summary,
            runtime_health={"id": "product_loop_coordinator", "status": status, "reason": reason},
            tool_calls=tool_calls,
            policy_decisions=policy_decisions,
            evidence_source="evidence_collected",
            qa_verdict=verdict,
        )

    def _run_result(
        self,
        loop: dict[str, Any],
        *,
        status: str,
        reason: str,
        evidence_package: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        loop = self._clear_run_active(loop)
        result = {
            "status": status,
            "reason": reason,
            "loop": loop,
            "resumable": not is_terminal(loop["state"]),
            "allowedNextStates": sorted(ALLOWED_TRANSITIONS[loop["state"]]),
            "transitions": self.repository.list_transitions(loop["id"]),
        }
        if evidence_package:
            result["evidencePackage"] = evidence_package
        return result

    def _complete_plan_only(
        self,
        loop: dict[str, Any],
        *,
        actor: str,
        thread_id: str,
        product_owner_output_id: str,
        backlog_artifact_id: str,
        agent_tasks: list[dict[str, Any]],
        team_schedule: dict[str, Any],
        team_assignments: list[dict[str, Any]],
        evidence_ids: list[str],
    ) -> dict[str, Any]:
        reason = "Product plan is ready without runtime execution."
        evidence = self._record_run_evidence(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            stage="planning",
            status="plan_ready",
            reason=reason,
            details={
                "planOnly": True,
                "productOwnerOutputId": product_owner_output_id,
                "backlogArtifactId": backlog_artifact_id,
                "agentTaskIds": [task["id"] for task in agent_tasks],
                "teamSchedule": team_schedule.get("summary") or {},
                "agentAssignmentIds": [assignment["id"] for assignment in team_assignments],
            },
            artifact_ids=[backlog_artifact_id],
        )
        context_patch = self._durable_run_patch(
            loop,
            {
                "status": "plan_ready",
                "planOnly": True,
                "planOnlyResult": {
                    "productOwnerOutputId": product_owner_output_id,
                    "backlogArtifactId": backlog_artifact_id,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                    "agentAssignmentIds": [assignment["id"] for assignment in team_assignments],
                    "teamSchedule": team_schedule.get("summary") or {},
                    "evidencePackageId": evidence["id"],
                },
            },
            evidence_package_ids=[*evidence_ids, evidence["id"]],
        )
        planned = self._write_context_entries(loop, context_patch)
        self._record_loop_event(
            project_id=planned["projectId"],
            event_type="product_loop.plan_ready",
            loop_id=planned["id"],
            payload={
                "status": "plan_ready",
                "planOnly": True,
                "productOwnerOutputId": product_owner_output_id,
                "backlogArtifactId": backlog_artifact_id,
                "evidencePackageId": evidence["id"],
            },
            thread_id=thread_id,
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type="plan_ready",
            agent_role="aido_lead",
            payload={
                "loopId": planned["id"],
                "status": "plan_ready",
                "evidencePackageId": evidence["id"],
            },
        )
        return self._run_result(planned, status="plan_ready", reason=reason, evidence_package=evidence)

    def _block_run(
        self,
        loop: dict[str, Any],
        *,
        stage: str,
        reason: str,
        actor: str,
        details: dict[str, Any] | None = None,
        durable_context: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if stage == "resource_manager":
            resource_approval = self._create_resource_approval_request(
                loop=loop,
                thread_id=thread_id,
                details=details or {},
            )
            if resource_approval is not None:
                durable_context = {**(durable_context or {}), "resourceApproval": resource_approval}
        evidence = self._record_run_evidence(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            stage=stage,
            status="blocked",
            reason=reason,
            details=details,
        )
        context_patch = self._durable_run_patch(
            loop,
            {
                **redact_secrets(durable_context or {}),
                "status": "blocked",
                "blockedStage": stage,
                "blockedReason": reason,
                stage: redact_secrets(details or {}),
            },
            evidence_package_ids=[evidence["id"]],
        )
        blocked = self._transition_run_state(
            loop,
            to_state=BLOCKED_STATE,
            reason=reason,
            trigger=f"{stage}_blocked",
            actor=actor,
            context_patch=context_patch,
            metadata={"blockedStage": stage, "evidencePackageId": evidence["id"]},
            thread_id=thread_id,
        )
        self._create_blocker_remediations_best_effort(
            project_id=blocked["projectId"],
            thread_id=thread_id,
            loop_id=blocked["id"],
            stage=stage,
            reason=reason,
            details=details or {},
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type="blocked",
            agent_role="aido_lead",
            payload={
                "loopId": blocked["id"],
                "stage": stage,
                "reason": reason,
                "evidencePackageId": evidence["id"],
            },
        )
        return self._run_result(blocked, status="blocked", reason=reason, evidence_package=evidence)

    def _create_resource_approval_request(
        self,
        *,
        loop: dict[str, Any],
        thread_id: str | None,
        details: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Publica en /approvals la selección de recursos que quedó bloqueada esperando aprobación.

        Sin esto la cola de aprobaciones queda vacía y el operador solo descubre el bloqueo dentro
        del thread. Devuelve la referencia pendiente para el contexto durable, ``None`` cuando el
        bloqueo no tiene selección aprobable, y un marcador ``unavailable`` si la persistencia
        falla: un fallo aquí jamás debe impedir el bloqueo (la remediación sigue disponible).
        """
        raw_blockers = details.get("resourceBlockers")
        approvals: list[dict[str, Any]] = []
        for item in raw_blockers if isinstance(raw_blockers, list) else []:
            if not isinstance(item, dict):
                continue
            decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
            selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else None
            if not selected or not self._resource_decision_has_pending_approval(decision):
                continue
            approvals.append(
                {
                    "role": item.get("role"),
                    "taskId": item.get("taskId"),
                    "providerId": selected.get("providerId"),
                    "model": selected.get("model"),
                    "runtime": selected.get("runtime"),
                    "selected": selected,
                    "decisionReason": decision.get("decisionReason"),
                    "estimatedCostUsd": decision.get("estimatedCostUsd"),
                    "costTier": decision.get("costTier"),
                }
            )
        if not approvals:
            return None
        payload = redact_secrets(
            {
                "loopId": loop["id"],
                "threadId": thread_id,
                "resourceApprovals": approvals,
            }
        )
        try:
            job = self.jobs.create_job(
                project_id=loop["projectId"],
                kind="product_loop_resource_approval",
                status="approval_required",
                payload=payload,
            )["job"]
            action = self.jobs.create_action_request(
                job_id=job["id"],
                project_id=loop["projectId"],
                action_type="product_loop.approve_resource_decision",
                risk_level="medium",
                command="approve ai resource selection",
                payload=payload,
                reason="AIResourceManager selected a resource that requires approval before execution.",
            )
        except Exception as error:
            return {"status": "unavailable", "reason": redact_secrets(str(error))}
        return {"status": "pending", "jobId": job["id"], "actionRequestId": action["id"]}

    def _ensure_delivery_agents(self, project_id: str) -> list[dict[str, str]]:
        from local_control_center.agents.team_bootstrap import bootstrap_base_team_if_needed

        bootstrap_base_team_if_needed(self.connection)
        return [
            {"agentId": profile["id"], "role": profile["role"], "projectId": project_id}
            for profile in self.agents.list_agent_profiles()
        ]

    def _create_thread(
        self,
        *,
        project_id: str,
        message: str,
        title: str | None,
        session_id: str | None,
        thread_id: str | None = None,
        message_id: str | None = None,
        message_metadata: dict[str, Any] | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        threads = ThreadsRepository(self.connection)
        clean_title = title or message.strip().splitlines()[0][:80] or "Product Loop"
        user_message_metadata = {
            **redact_secrets(message_metadata or {}),
            "source": "product_loop",
        }
        if session_id:
            user_message_metadata["legacySessionId"] = session_id
        if thread_id:
            thread = threads.get_thread(thread_id)
            if thread["projectId"] != project_id:
                raise ProductLoopTransitionError("Thread project does not match the Product Loop project.")
            resolved_message_id = message_id
            if not resolved_message_id:
                created_message = threads.append_message(
                    thread_id=thread_id,
                    kind="user",
                    author=actor,
                    content=message,
                    metadata=user_message_metadata,
                )
                resolved_message_id = created_message["id"]
            return {
                "projectThreadId": thread["id"],
                "messageId": resolved_message_id,
                "title": thread["title"],
            }

        thread = threads.create_thread(
            project_id=project_id,
            owner_type="workspace",
            owner_id=session_id or project_id,
            title=clean_title,
            metadata={
                "source": "product_loop",
                "legacySessionId": session_id,
            },
        )
        created_message = threads.append_message(
            thread_id=thread["id"],
            kind="user",
            author=actor,
            content=message,
            metadata=user_message_metadata,
        )
        return {
            "projectThreadId": thread["id"],
            "messageId": created_message["id"],
            "title": thread["title"],
        }

    @staticmethod
    def _memory_decision_resolved(request_meta: dict[str, Any]) -> bool:
        decision = request_meta.get("decision") if isinstance(request_meta.get("decision"), dict) else {}
        modes = [
            request_meta.get("mode"),
            request_meta.get("functionalityDecision"),
            request_meta.get("memoryMode"),
            decision.get("mode"),
            decision.get("functionalityDecision"),
            decision.get("memoryMode"),
            decision.get("userMode"),
            decision.get("resolution"),
        ]
        return any(str(mode or "").strip() in SIMILARITY_ACTIONS for mode in modes)

    def _existing_functionality_match(self, *, project_id: str, message: str) -> dict[str, Any] | None:
        matches = ThreadMemoryService(self.connection).find_existing_functionality(
            project_id=project_id,
            query=message,
            limit=1,
        )
        return matches[0] if matches else None

    def _block_existing_functionality(
        self,
        *,
        loop: dict[str, Any],
        thread_id: str,
        functionality: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        threads = ThreadsRepository(self.connection)
        options = list(SIMILARITY_ACTIONS)
        reason = (
            f"Existing functionality detected: {functionality['name']}. "
            "Choose whether to continue, improve, run a performance pass, or create a new thread anyway."
        )
        metadata = {
            "source": "functionality_registry",
            "functionalityId": functionality["id"],
            "sourceThreadId": functionality["sourceThreadId"],
            "score": functionality.get("score"),
            "reason": functionality.get("reason"),
        }
        request_message = threads.append_message(
            thread_id=thread_id,
            kind="decision_request",
            author="aido_lead",
            content=reason,
            metadata=metadata,
        )
        decision = threads.create_decision(
            thread_id=thread_id,
            message_id=request_message["id"],
            title="Existing functionality detected",
            prompt=reason,
            options=options,
            metadata=metadata,
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type="functionality_detected",
            agent_role="aido_lead",
            payload={**metadata, "decisionId": decision["id"]},
        )
        self._set_thread_status_best_effort(
            thread_id=thread_id,
            status="waiting_decision",
            reason="Existing functionality decision is required before execution.",
        )
        evidence = self._record_run_evidence(
            project_id=loop["projectId"],
            loop_id=loop["id"],
            stage="functionality_memory",
            status="blocked",
            reason=reason,
            details={"functionality": functionality, "decisionId": decision["id"]},
        )
        blocked = self._transition_run_state(
            loop,
            to_state=BLOCKED_STATE,
            reason=reason,
            trigger="functionality_memory_blocked",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {
                    "status": "blocked",
                    "blockedStage": "functionality_memory",
                    "blockedReason": reason,
                    "existingFunctionality": functionality,
                    "decisionId": decision["id"],
                },
                evidence_package_ids=[evidence["id"]],
            ),
            metadata={"blockedStage": "functionality_memory", "decisionId": decision["id"]},
            thread_id=thread_id,
        )
        self._create_blocker_remediations_best_effort(
            project_id=blocked["projectId"],
            thread_id=thread_id,
            loop_id=blocked["id"],
            stage="functionality_memory",
            reason=reason,
            details={
                "decisionId": decision["id"],
                "functionalityId": functionality["id"],
                "sourceThreadId": functionality["sourceThreadId"],
                "score": functionality.get("score"),
                "options": options,
            },
        )
        return self._run_result(blocked, status="blocked", reason=reason, evidence_package=evidence)

    def _external_evidence_ids(self, payload: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        evidence = payload.get("evidencePackage")
        if isinstance(evidence, dict) and isinstance(evidence.get("id"), str):
            ids.append(evidence["id"])
        ids.extend(
            str(payload[key])
            for key in ("evidencePackageId", "evidenceId")
            if isinstance(payload.get(key), str)
        )
        return ids

    def _write_json_artifact(
        self,
        *,
        root: Path,
        project_id: str,
        name: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_id = f"artifact-{uuid.uuid4()}"
        content = json_dumps(redact_secrets(payload))
        written = write_text_artifact(root=root, artifact_id=artifact_id, suffix=".json", content=content)
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind=kind,
            path=written["path"],
            content_hash=written["hash"],
            metadata={
                "name": name,
                "source": "product_loop_coordinator",
                "mimeType": "application/json",
                "hashAlgorithm": "sha256",
                "sizeBytes": written["sizeBytes"],
            },
        )

    def _attach_thread_artifacts(self, *, thread_id: str | None, artifacts: list[dict[str, Any]]) -> None:
        if not thread_id:
            return
        threads = ThreadsRepository(self.connection)
        for artifact in artifacts:
            metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
            try:
                threads.attach_artifact(
                    thread_id=thread_id,
                    kind=str(artifact.get("kind") or "generic_artifact"),
                    title=str(metadata.get("name") or artifact.get("id") or "artifact"),
                    artifact_id=str(artifact["id"]),
                    payload={"artifact": artifact},
                )
            except (KeyError, ValueError):
                return

    def _project_is_existing(self, project_id: str) -> bool:
        project = ProjectsRepository(self.connection).get_project(project_id)
        path = str(project.get("path") or "").strip()
        return bool(path and Path(path).exists())

    def _product_owner_output(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ProductOwnerOutputValidationError("ProductOwnerAgent result must be a JSON object.")
        raw_output = result.get("output")
        if not isinstance(raw_output, dict):
            reason = str(result.get("reason") or "").strip()
            if reason.startswith("ProductOwnerAgent"):
                raise ProductOwnerOutputValidationError(reason)
            suffix = f": {reason}" if reason else "."
            raise ProductOwnerOutputValidationError(f"ProductOwnerAgent output must be a JSON object{suffix}")
        output = dict(raw_output)
        for key in (
            "status",
            "summary",
            "confidence",
            "questions",
            "assumptions",
            "decisions",
            "productBriefPatch",
            "epics",
            "userStories",
            "risks",
            "recommendedNextAction",
        ):
            if key not in output and key in result:
                output[key] = result[key]
        if "productBriefPatch" not in output:
            output["productBriefPatch"] = result.get("brief") or {}
        validated = ProductOwnerAgent().validate_output(output)
        for key, value in output.items():
            validated.setdefault(key, value)
        return validated

    def _has_actionable_product_owner_input(
        self,
        *,
        questions: list[Any],
        decisions: list[Any],
    ) -> bool:
        for item in [*questions, *decisions]:
            if not isinstance(item, dict):
                continue
            if len(self._question_options(item)) >= 2:
                return True
        return False

    def _product_owner_flow_status(self, result: dict[str, Any], output: dict[str, Any] | None = None) -> str:
        output = output if output is not None else self._product_owner_output(result)
        status = str(result.get("status") or "").strip().lower()
        output_status = str(output.get("status") or "").strip().lower()
        questions = output.get("questions") or result.get("questions") or []
        decisions = (
            output.get("decisions") or result.get("blockingDecisions") or result.get("decisions") or []
        )
        if status in {"runtime_unavailable", "failed_validation", "failed"}:
            return "blocked"
        if status in {"needs_input", "questions_required"} or output_status in {
            "needs_input",
            "questions_required",
        }:
            if not self._has_actionable_product_owner_input(questions=questions, decisions=decisions):
                raise ProductOwnerOutputValidationError(
                    "ProductOwnerAgent needs_input output must include an actionable question or decision with options."
                )
            return "needs_input"
        if status == "blocked" and (questions or decisions):
            if not self._has_actionable_product_owner_input(questions=questions, decisions=decisions):
                raise ProductOwnerOutputValidationError(
                    "ProductOwnerAgent blocked output must include an actionable question or decision with options."
                )
            return "needs_input"
        if status == "completed":
            return "backlog_ready"
        if status == "scope_is_clear" or output_status == "scope_is_clear":
            return "brief_ready"
        if status == "brief_ready" or output_status == "brief_ready":
            return "brief_ready"
        if status in {"backlog_ready", "completed"} or output_status in {"backlog_ready", "completed"}:
            return "backlog_ready"
        return "blocked"

    def _ensure_product_initiative(
        self,
        *,
        project_id: str,
        message: str,
        title: str,
        result: dict[str, Any],
        output: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Resuelve la iniciativa del turno, reusando la que ya cubre el hilo antes de crear una nueva.

        Sin la reutilización por hilo cada turno arrancaría con una iniciativa vacía y el
        ProductOwner volvería a preguntar lo que ya se respondió.
        """
        initiative = result.get("initiative")
        if isinstance(initiative, dict) and initiative.get("id"):
            try:
                return self._stamp_initiative_thread(
                    self.discovery.get_initiative(str(initiative["id"])), thread_id
                )
            except KeyError:
                pass
        initiative_id = result.get("initiativeId") or output.get("initiativeId")
        if initiative_id:
            try:
                return self._stamp_initiative_thread(
                    self.discovery.get_initiative(str(initiative_id)), thread_id
                )
            except KeyError:
                pass
        if thread_id:
            existing = self.discovery.find_initiative_by_thread(project_id, thread_id)
            if existing is not None:
                return existing
        metadata: dict[str, Any] = {"source": "product_loop_coordinator"}
        if thread_id:
            metadata["threadId"] = thread_id
        return self.discovery.create_initiative(
            {
                "projectId": project_id,
                "title": title,
                "summary": output.get("summary") or message,
                "status": "discovery",
                "priority": "medium",
                "owner": PRODUCT_OWNER_AGENT_ID,
                "metadata": metadata,
            }
        )

    def _stamp_initiative_thread(self, initiative: dict[str, Any], thread_id: str | None) -> dict[str, Any]:
        """Estampa ``threadId`` en una iniciativa reusada que aún no lo tiene.

        El Runner real crea la iniciativa sin ``metadata.threadId``; sin este sello,
        ``find_initiative_by_thread`` falla el turno siguiente y el hilo arranca con una iniciativa
        nueva, perdiendo brief, preguntas y respuestas previas (el PO vuelve a preguntar todo).
        """
        if not thread_id:
            return initiative
        metadata = initiative.get("metadata") if isinstance(initiative.get("metadata"), dict) else {}
        if metadata.get("threadId") == thread_id:
            return initiative
        try:
            return self.discovery.update_initiative(
                initiative["id"], {"metadata": {**metadata, "threadId": thread_id}}
            )
        except KeyError:
            return initiative

    def _brief_payload(self, *, title: str, result: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
        brief = dict(result.get("brief") or output.get("productBriefPatch") or {})
        return {
            "title": str(brief.get("title") or title),
            "summary": str(brief.get("summary") or output.get("summary") or ""),
            "problemStatement": str(brief.get("problemStatement") or ""),
            "goals": list(brief.get("goals") or []),
            "targetUsers": list(brief.get("targetUsers") or []),
            "successMetrics": list(brief.get("successMetrics") or []),
            "scope": str(brief.get("scope") or ""),
            "outOfScope": str(brief.get("outOfScope") or ""),
        }

    def _persist_product_brief(
        self,
        *,
        project_id: str,
        initiative_id: str,
        title: str,
        result: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        brief = result.get("brief")
        if isinstance(brief, dict) and brief.get("id"):
            try:
                return self.discovery.get_product_brief(str(brief["id"]))
            except KeyError:
                pass
        payload = self._brief_payload(title=title, result=result, output=output)
        return self.discovery.upsert_product_brief(
            {
                "projectId": project_id,
                "initiativeId": initiative_id,
                "title": payload["title"],
                "status": "in_review",
                "summary": payload["summary"],
                "problemStatement": payload["problemStatement"],
                "goals": payload["goals"],
                "targetUsers": payload["targetUsers"],
                "successMetrics": payload["successMetrics"],
                "scope": payload["scope"],
                "outOfScope": payload["outOfScope"],
                "changeSummary": "ProductLoopCoordinator persisted ProductOwnerAgent brief.",
                "authoredBy": PRODUCT_OWNER_AGENT_ID,
            }
        )

    def _persist_product_owner_output_record(
        self,
        *,
        project_id: str,
        initiative_id: str,
        brief_id: str,
        output: dict[str, Any],
        result: dict[str, Any],
        artifact_id: str | None,
    ) -> dict[str, Any]:
        existing = result.get("productOwnerOutput")
        if isinstance(existing, dict) and existing.get("id"):
            try:
                return self.discovery.get_product_owner_output(str(existing["id"]))
            except KeyError:
                pass
        return self.discovery.create_product_owner_output(
            {
                "projectId": project_id,
                "initiativeId": initiative_id,
                "briefId": brief_id,
                "status": str(output.get("status") or result.get("status") or "blocked"),
                "summary": str(output.get("summary") or ""),
                "confidence": str(output.get("confidence") or "low"),
                "questions": output.get("questions") or [],
                "assumptions": output.get("assumptions") or [],
                "decisions": output.get("decisions") or [],
                "productBriefPatch": output.get("productBriefPatch") or {},
                "epics": output.get("epics") or [],
                "userStories": output.get("userStories") or [],
                "risks": output.get("risks") or [],
                "recommendedNextAction": str(output.get("recommendedNextAction") or ""),
                "runtimeId": str((result.get("runtime") or {}).get("id") or ""),
                "outputArtifactId": artifact_id or str(result.get("outputArtifactId") or ""),
                "metadata": {"source": "product_loop_coordinator"},
            }
        )

    def _question_options(self, question: dict[str, Any]) -> list[str]:
        metadata = question.get("metadata") if isinstance(question.get("metadata"), dict) else {}
        options = question.get("options") or metadata.get("options") or []
        return [str(item) for item in options if str(item).strip()]

    def _runner_persisted(
        self, result: dict[str, Any] | None, key: str, prefix: str
    ) -> list[dict[str, Any]] | None:
        """Registros con id que el Runner real ya persistió, o ``None`` si no persistió (stub/tests).

        Cuando existen, son la fuente de verdad y se reusan; el ``output`` id-less del modelo se usa solo
        como camino de respaldo para el runner de prueba, que no toca la base.
        """
        if not isinstance(result, dict):
            return None
        records = [
            record
            for record in result.get(key) or []
            if isinstance(record, dict) and str(record.get("id") or "").startswith(prefix)
        ]
        return records or None

    def _reuse_persisted(self, item: dict[str, Any], prefix: str, getter: Any) -> dict[str, Any] | None:
        record_id = str(item.get("id") or "")
        if not record_id.startswith(prefix):
            return None
        try:
            return getter(record_id)
        except KeyError:
            return None

    def _decision_options_with_recommendation_fallback(
        self, record: dict[str, Any], item: dict[str, Any]
    ) -> list[str]:
        """Opciones para la decisión de hilo del PO: las ofrecidas, o la recomendación como única.

        Una decisión sin opciones nunca debe quedar sin nada preseleccionable: si el PO no ofreció
        opciones pero sí una recomendación (o un valor por defecto), esa recomendación se ofrece como
        la única opción; el texto libre de la barra de ejecución sigue disponible de todos modos.
        """
        options = self._question_options(record) or self._question_options(item)
        if options:
            return options
        record_metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        recommendation = str(
            record.get("decision")
            or record_metadata.get("recommendation")
            or item.get("recommendation")
            or item.get("defaultDecision")
            or ""
        ).strip()
        return [recommendation] if recommendation else []

    def _ensure_product_owner_thread_decision(
        self,
        threads: Any,
        thread_id: str,
        *,
        record_id: str,
        meta_key: str,
        title: str,
        prompt: str,
        options: list[str],
        source_message_id: str | None,
    ) -> None:
        """Crea la decisión de hilo para un registro del PO una sola vez (idempotente por ``record_id``)."""
        for decision in threads.list_decisions(thread_id):
            metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
            if metadata.get(meta_key) == record_id:
                return
        decision_metadata = {"source": PRODUCT_OWNER_AGENT_ID, meta_key: record_id}
        if source_message_id:
            decision_metadata["sourceMessageId"] = source_message_id
        threads.create_decision(
            thread_id=thread_id, title=title, prompt=prompt, options=options, metadata=decision_metadata
        )

    def _persist_clarification_questions(
        self,
        *,
        project_id: str,
        initiative_id: str,
        output: dict[str, Any],
        thread_id: str | None,
        source_message_id: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Reusa las preguntas que el Runner real ya persistió; solo crea cuando no persistió (stub).

        El Runner ya inserta las preguntas de aclaración; iterar el ``output`` id-less del modelo volvía
        a insertarlas (filas duplicadas cada turno, y las del Runner quedaban ``open`` contaminando el
        prompt siguiente). Aquí se reusa el registro persistido por id y se surface como decisión de hilo
        una sola vez.
        """
        records: list[dict[str, Any]] = []
        threads = ThreadsRepository(self.connection) if thread_id else None
        persisted = self._runner_persisted(result, "questions", "clarification-question-")
        for item in persisted if persisted is not None else (output.get("questions") or []):
            if not isinstance(item, dict):
                continue
            record = self._reuse_persisted(
                item, "clarification-question-", self.discovery.get_clarification_question
            )
            if record is None:
                question_text = str(item.get("question") or item.get("prompt") or "").strip()
                if not question_text:
                    continue
                record = self.discovery.create_clarification_question(
                    {
                        "projectId": project_id,
                        "initiativeId": initiative_id,
                        "question": question_text,
                        "priority": item.get("priority") or "high"
                        if item.get("blocking", True)
                        else "medium",
                        "askedBy": PRODUCT_OWNER_AGENT_ID,
                        "metadata": {
                            "source": PRODUCT_OWNER_AGENT_ID,
                            "category": item.get("category") or "product",
                            "whyItMatters": item.get("whyItMatters") or "",
                            "blocking": bool(item.get("blocking", True)),
                            "options": self._question_options(item),
                            "recommendation": item.get("recommendation") or "",
                            "defaultDecision": item.get("defaultDecision") or "",
                            "confidence": item.get("confidence") or "low",
                        },
                    }
                )
            records.append(record)
            if threads and thread_id:
                question_text = str(record.get("question") or item.get("question") or "")
                self._ensure_product_owner_thread_decision(
                    threads,
                    thread_id,
                    record_id=record["id"],
                    meta_key="clarificationQuestionId",
                    title=question_text[:120],
                    prompt=question_text,
                    options=self._decision_options_with_recommendation_fallback(record, item),
                    source_message_id=source_message_id,
                )
        return records

    def _persist_product_decisions(
        self,
        *,
        project_id: str,
        initiative_id: str,
        brief_id: str,
        output: dict[str, Any],
        thread_id: str | None,
        source_message_id: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Reusa las decisiones que el Runner real ya persistió; solo crea cuando no persistió (stub).

        Igual que las preguntas, iterar el ``output`` id-less duplicaba las filas que el Runner ya
        insertó y las decisiones escaladas aparecían dos veces en el hilo. Se reusa el registro por id;
        sus opciones vienen del ``metadata.options`` persistido (o del modelo en el camino de respaldo).
        """
        records: list[dict[str, Any]] = []
        threads = ThreadsRepository(self.connection) if thread_id else None
        persisted = self._runner_persisted(result, "blockingDecisions", "product-decision-")
        for item in persisted if persisted is not None else (output.get("decisions") or []):
            if not isinstance(item, dict):
                continue
            record = self._reuse_persisted(item, "product-decision-", self.discovery.get_product_decision)
            if record is None:
                title = str(item.get("title") or item.get("decision") or "").strip()
                if not title:
                    continue
                blocking = bool(item.get("blocking", False))
                raw_status = str(item.get("status") or "").strip().lower()
                status = raw_status or ("proposed" if blocking else "accepted")
                record = self.discovery.create_product_decision(
                    {
                        "projectId": project_id,
                        "initiativeId": initiative_id,
                        "briefId": brief_id,
                        "title": title,
                        "status": status,
                        "context": item.get("question") or title,
                        "decision": item.get("decision") or item.get("recommendation") or "",
                        "rationale": item.get("rationale") or "",
                        "consequences": item.get("consequences") or [],
                        "decidedBy": PRODUCT_OWNER_AGENT_ID if status in {"accepted", "resolved"} else "",
                        "decidedAt": utc_now() if status in {"accepted", "resolved"} else None,
                        "metadata": {
                            "source": PRODUCT_OWNER_AGENT_ID,
                            "blocking": blocking,
                            "confidence": item.get("confidence") or "low",
                            "category": item.get("category") or item.get("type") or "",
                            "impact": item.get("impact") or item.get("risk") or item.get("severity") or "",
                            "requiresResearch": bool(item.get("requiresResearch", False)),
                            "options": self._question_options(item),
                        },
                    }
                )
            records.append(record)
            status = str(record.get("status") or "").strip().lower()
            if threads and thread_id and status not in {"accepted", "resolved"}:
                title = str(record.get("title") or item.get("title") or "")
                self._ensure_product_owner_thread_decision(
                    threads,
                    thread_id,
                    record_id=record["id"],
                    meta_key="productDecisionId",
                    title=title[:120],
                    prompt=str(record.get("context") or item.get("question") or title),
                    options=self._decision_options_with_recommendation_fallback(record, item),
                    source_message_id=source_message_id,
                )
        return records

    def _pending_product_owner_thread_decisions(self, thread_id: str | None) -> list[dict[str, Any]]:
        if not thread_id:
            return []
        pending: list[dict[str, Any]] = []
        for decision in ThreadsRepository(self.connection).list_decisions(thread_id):
            metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
            if decision["status"] != "pending" or metadata.get("source") != PRODUCT_OWNER_AGENT_ID:
                continue
            pending.append(
                {
                    "decisionId": decision["id"],
                    "title": decision["title"],
                    "prompt": decision["prompt"],
                    "options": decision.get("options") or [],
                    "clarificationQuestionId": metadata.get("clarificationQuestionId"),
                    "productDecisionId": metadata.get("productDecisionId"),
                    "initiativeId": metadata.get("initiativeId"),
                }
            )
        return pending

    def _persist_product_owner_backlog(
        self,
        *,
        project_id: str,
        output: dict[str, Any],
        result: dict[str, Any],
        product_owner_output_id: str | None,
    ) -> list[dict[str, Any]]:
        persisted = result.get("epics")
        if (
            isinstance(persisted, list)
            and persisted
            and isinstance(persisted[0], dict)
            and "epic" in persisted[0]
        ):
            return persisted
        if not output.get("epics") or not output.get("userStories"):
            return []
        return persist_product_owner_backlog(
            self.backlog,
            project_id=project_id,
            output=output,
            product_owner_output_id=product_owner_output_id,
        )

    def _stories_from_backlog(self, backlog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stories: list[dict[str, Any]] = []
        for epic_group in backlog:
            for item in epic_group.get("stories") or []:
                story = item.get("story") if isinstance(item, dict) else None
                if isinstance(story, dict):
                    stories.append(story)
        return stories

    def _story_specs_for_tasks(
        self, agent_tasks: list[dict[str, Any]], *, for_role: str | None = None
    ) -> str | None:
        """Renderiza el spec ejecutable (epica/HU/criterios/roles) de las historias asignadas.

        Devuelve None cuando no hay historias resolubles para no alterar el prompt legado.
        ``for_role`` filtra las responsabilidades al rol destinatario (el developer sigue
        recibiendo el spec completo para la auditoria cruzada).
        """
        story_ids: list[str] = []
        for task in agent_tasks or []:
            story_id = str(task.get("storyId") or "").strip()
            if story_id and story_id not in story_ids:
                story_ids.append(story_id)
        specs: list[dict[str, Any]] = []
        for story_id in story_ids:
            try:
                specs.append(build_story_spec(self.backlog, story_id))
            except KeyError:
                continue
        if not specs:
            return None
        return render_story_spec_prompt(specs, for_role=for_role)

    def _acceptance_criteria_from_backlog(self, backlog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        criteria: list[dict[str, Any]] = []
        for epic_group in backlog:
            for item in epic_group.get("stories") or []:
                if not isinstance(item, dict):
                    continue
                story = item.get("story") if isinstance(item.get("story"), dict) else {}
                story_id = story.get("id")
                for index, criterion in enumerate(item.get("acceptanceCriteria") or [], start=1):
                    if isinstance(criterion, dict):
                        criteria.append(criterion)
                    else:
                        criteria.append(
                            {
                                "id": f"{story_id}-ac-{index}",
                                "storyId": story_id,
                                "criterion": str(criterion),
                            }
                        )
        return criteria

    @staticmethod
    def _qa_rework_feedback(qa_results: list[Any]) -> str:
        """Resume los comandos QA no aprobados en texto compacto y redactado para el rework.

        Acota stderr a la cola (donde vive el error real) y el número de comandos detallados a
        ``REWORK_FEEDBACK_COMMAND_LIMIT``: este texto se reenvía en cada ronda de rework y por cada
        failover de runtime, así que una suite con decenas de rojos inflaba el prompt del developer.
        """
        failing = [
            result
            for result in qa_results
            if isinstance(result, dict) and str(result.get("status") or "").strip().lower() != "passed"
        ]
        lines: list[str] = []
        for result in failing[:REWORK_FEEDBACK_COMMAND_LIMIT]:
            status = str(result.get("status") or "").strip().lower()
            label = str(result.get("label") or result.get("command") or "qa")
            line = f"- {label}: status={status}, exitCode={result.get('exitCode')}"
            stderr_tail = str(result.get("stderr") or "").strip()[-400:]
            if stderr_tail:
                line += f"\n  stderr (tail): {stderr_tail}"
            lines.append(line)
        omitted = len(failing) - REWORK_FEEDBACK_COMMAND_LIMIT
        if omitted > 0:
            lines.append(f"- (+{omitted} more failing commands; run the QA suite locally for the full list)")
        summary = "\n".join(lines) or "QA failed without per-command detail."
        return str(redact_secrets(summary))

    def _generate_agent_tasks(
        self,
        *,
        project_id: str,
        loop_id: str,
        backlog: list[dict[str, Any]],
        product_owner_output_id: str | None,
        technical_lead_runner: Any | None,
        team_schedule: dict[str, Any] | None = None,
        product_owner_output: dict[str, Any] | None = None,
        assessment_result: dict[str, Any] | None = None,
        git_state: dict[str, Any] | None = None,
        plan_sink: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        stories = self._stories_from_backlog(backlog)
        existing = [task for story in stories for task in self.backlog.list_agent_tasks(story_id=story["id"])]
        if existing:
            return existing
        payload = {
            "projectId": project_id,
            "loopId": loop_id,
            "productOwnerOutputId": product_owner_output_id,
            "backlog": backlog,
            "userStories": stories,
            "acceptanceCriteria": self._acceptance_criteria_from_backlog(backlog),
            "productBrief": (product_owner_output or {}).get("productBriefPatch")
            or (product_owner_output or {}).get("productBrief")
            or {},
            "projectAssessment": {
                **(assessment_result or {}),
                "changedFiles": (git_state or {}).get("changedFiles") or [],
            },
            "intentClassification": (team_schedule or {}).get("intent") or {},
            "risk": (team_schedule or {}).get("risk")
            or ((team_schedule or {}).get("intent") or {}).get("risk"),
            "availableTeam": (team_schedule or {}).get("roles") or [],
            "teamSchedule": team_schedule or {},
        }
        if technical_lead_runner is not None and hasattr(technical_lead_runner, "generate_agent_tasks"):
            planned = technical_lead_runner.generate_agent_tasks(payload)
        else:
            planned = TechnicalLeadPlanner().plan(payload)
        if plan_sink is not None and isinstance(planned, dict):
            plan_sink.update(planned)
        if isinstance(planned, dict):
            specs = planned.get("agent_tasks") or planned.get("agentTasks") or []
            dependency_specs = planned.get("task_dependencies") or planned.get("taskDependencies") or []
        else:
            specs = planned
            dependency_specs = []
        if technical_lead_runner is not None and hasattr(technical_lead_runner, "generate_agent_tasks"):
            specs, dependency_specs, fallback_story_ids = self._with_deterministic_fallback(
                payload, stories, list(specs or []), list(dependency_specs or [])
            )
            if plan_sink is not None and fallback_story_ids:
                plan_sink["technicalLeadFallbackStoryIds"] = fallback_story_ids
        tasks: list[dict[str, Any]] = []
        valid_specs: list[tuple[dict[str, Any], str, str]] = []
        for spec in specs or []:
            if not isinstance(spec, dict):
                continue
            story_id = str(spec.get("storyId") or "").strip()
            if not story_id:
                continue
            role = str(spec.get("role") or "").strip()
            if not role:
                raise ValueError(
                    f"TechnicalLeadPlanner generated agent_task missing role for story {story_id}."
                )
            valid_specs.append((spec, story_id, role))
        planned_to_persisted: dict[str, str] = {}
        for spec, story_id, role in valid_specs:
            planned_task_id = str(spec.get("id") or "").strip()
            planner_metadata = {
                key: spec.get(key)
                for key in (
                    "goal",
                    "scope",
                    "filesLikely",
                    "acceptanceRefs",
                    "outputSchema",
                    "requiredTools",
                    "runtimePreference",
                    "reviewerRole",
                    "qualityGates",
                    "risk",
                )
                if key in spec
            }
            task = self.backlog.create_agent_task(
                {
                    "projectId": project_id,
                    "storyId": story_id,
                    "title": spec.get("title") or "Implement user story",
                    "description": spec.get("description") or "",
                    "role": role,
                    "category": spec.get("category") or "implementation",
                    "status": spec.get("status") or "todo",
                    "priority": spec.get("priority") or "medium",
                    "estimateHours": spec.get("estimateHours"),
                    "metadata": {
                        **dict(spec.get("metadata") or {}),
                        **planner_metadata,
                        "source": "technical_lead",
                        "loopId": loop_id,
                        "productOwnerOutputId": product_owner_output_id,
                        "technicalLeadTaskId": planned_task_id,
                    },
                }
            )
            if planned_task_id:
                planned_to_persisted[planned_task_id] = task["id"]
            tasks.append({**spec, **task})
        for dependency in dependency_specs or []:
            if not isinstance(dependency, dict):
                continue
            task_id = planned_to_persisted.get(str(dependency.get("taskId") or ""))
            depends_on_task_id = planned_to_persisted.get(str(dependency.get("dependsOnTaskId") or ""))
            if not task_id or not depends_on_task_id or task_id == depends_on_task_id:
                continue
            self.backlog.create_task_dependency(
                {
                    "projectId": project_id,
                    "taskId": task_id,
                    "dependsOnTaskId": depends_on_task_id,
                    "type": dependency.get("type") or "blocks",
                    "reason": dependency.get("reason") or "TechnicalLeadPlanner task ordering.",
                    "metadata": {
                        "source": "technical_lead",
                        "loopId": loop_id,
                        "productOwnerOutputId": product_owner_output_id,
                    },
                }
            )
        return tasks

    def _with_deterministic_fallback(
        self,
        payload: dict[str, Any],
        stories: list[dict[str, Any]],
        specs: list[Any],
        dependency_specs: list[Any],
    ) -> tuple[list[Any], list[Any], list[str]]:
        """Completa con el ``TechnicalLeadPlanner`` determinista las historias que el TL LLM dejó sin tareas.

        Decisión de arquitecto 2026-09-22: una historia del PO sin tareas no bloquea si el planner
        determinista (el planner por defecto) la cubre; sus tareas quedan marcadas con
        ``metadata.technicalLeadFallback``. Si tampoco la cubre, la fase por historia bloquea en
        ``technical_lead`` (``phases/story_loop.py``). Un error del planner se propaga y la fase de
        planificación bloquea en ``technical_lead`` (``phases/team_planning.plan_team_and_resources``).
        """
        covered = {str(spec.get("storyId") or "").strip() for spec in specs if isinstance(spec, dict)}
        uncovered = [story for story in stories if str(story.get("id") or "") not in covered]
        if not uncovered:
            return specs, dependency_specs, []
        fallback = TechnicalLeadPlanner().plan({**payload, "userStories": uncovered})
        fallback_specs = [
            {**spec, "metadata": {**dict(spec.get("metadata") or {}), "technicalLeadFallback": True}}
            for spec in fallback.get("agent_tasks") or []
            if isinstance(spec, dict)
        ]
        planned_story_ids = {str(spec.get("storyId") or "").strip() for spec in fallback_specs}
        fallback_story_ids = [
            str(story["id"]) for story in uncovered if str(story["id"]) in planned_story_ids
        ]
        return (
            [*specs, *fallback_specs],
            [*dependency_specs, *(fallback.get("task_dependencies") or [])],
            fallback_story_ids,
        )

    def _team_schedule(
        self,
        *,
        message: str,
        request_meta: dict[str, Any],
        output: dict[str, Any],
        assessment_result: dict[str, Any] | None,
        git_state: dict[str, Any] | None,
        agent_tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        classifier = IntentClassifier()
        intent = classifier.classify(
            IntentClassificationInput(
                prompt=message,
                project_assessment=assessment_result or {},
                changed_files=(git_state or {}).get("changedFiles") or [],
                git_state=git_state or {},
                user_mode=str(request_meta.get("userMode") or request_meta.get("user_mode") or "aido_decide"),
            )
        ).to_dict()
        tasks = agent_tasks or []
        mode = self._team_mode(request_meta)
        risk = self._team_risk(request_meta=request_meta, intent=intent, output=output, message=message)
        scope = self._team_scope(message=message, intent=intent, output=output, agent_tasks=tasks)
        plan = schedule_team(
            scope=scope,
            risk=risk,
            mode=mode,
            disabled_providers=disabled_provider_ids(self.connection),
        )
        return {**plan, "intent": intent}

    def _resource_role_policy(
        self,
        role: str,
        *,
        fallback_to_developer: bool = True,
    ) -> dict[str, Any]:
        return resource_selection_phase.resource_role_policy(
            self, role, fallback_to_developer=fallback_to_developer
        )

    def _trusted_remediation_resource_approval_marker(
        self,
        *,
        request_meta: dict[str, Any],
        source_loop: dict[str, Any],
        expected_action_type: str,
    ) -> bool:
        return resource_selection_phase.trusted_remediation_resource_approval_marker(
            self,
            request_meta=request_meta,
            source_loop=source_loop,
            expected_action_type=expected_action_type,
        )

    def _trusted_feedback_resource_approval_marker(
        self, *, request_meta: dict[str, Any], source_loop: dict[str, Any]
    ) -> bool:
        return resource_selection_phase.trusted_feedback_resource_approval_marker(
            self, request_meta=request_meta, source_loop=source_loop
        )

    def _sanitize_untrusted_resource_approval_metadata(self, request_meta: dict[str, Any]) -> dict[str, Any]:
        return resource_selection_phase.sanitize_untrusted_resource_approval_metadata(self, request_meta)

    def _team_schedule_with_resource_decisions(
        self,
        *,
        project_id: str,
        loop_id: str,
        request_meta: dict[str, Any],
        team_schedule: dict[str, Any],
        agent_tasks: list[dict[str, Any]],
        runtime_risk_review_id: str | None = None,
        product_owner_selected_resource: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return resource_selection_phase.team_schedule_with_resource_decisions(
            self,
            project_id=project_id,
            loop_id=loop_id,
            request_meta=request_meta,
            team_schedule=team_schedule,
            agent_tasks=agent_tasks,
            runtime_risk_review_id=runtime_risk_review_id,
            product_owner_selected_resource=product_owner_selected_resource,
            thread_id=thread_id,
        )

    def _record_assigned_provider_skipped(
        self,
        *,
        thread_id: str | None,
        loop_id: str,
        request_meta: dict[str, Any],
        team_role: str | None,
        role: str,
        decision: dict[str, Any],
    ) -> None:
        return resource_selection_phase.record_assigned_provider_skipped(
            self,
            thread_id=thread_id,
            loop_id=loop_id,
            request_meta=request_meta,
            team_role=team_role,
            role=role,
            decision=decision,
        )

    def _jev_provider_walk(
        self, project_id: str, request_meta: dict[str, Any], team_role: str | None
    ) -> list[str]:
        return resource_selection_phase.jev_provider_walk(self, project_id, request_meta, team_role)

    def _team_resource_request(
        self,
        *,
        project_id: str,
        loop_id: str,
        request_meta: dict[str, Any],
        team_schedule: dict[str, Any],
        role_plan: dict[str, Any],
        agent_tasks: list[dict[str, Any]],
        local_model_affinity: dict[str, str] | None = None,
        product_owner_provider_id: str | None = None,
        allowed_override: list[str] | None = None,
    ) -> AIResourceRequest:
        return resource_selection_phase.team_resource_request(
            self,
            project_id=project_id,
            loop_id=loop_id,
            request_meta=request_meta,
            team_schedule=team_schedule,
            role_plan=role_plan,
            agent_tasks=agent_tasks,
            local_model_affinity=local_model_affinity,
            product_owner_provider_id=product_owner_provider_id,
            allowed_override=allowed_override,
        )

    def _profile_by_role(self, project_id: str | None = None) -> dict[str, dict[str, Any]]:
        profiles = self.agents.list_agent_profiles(project_id=project_id)
        by_role: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            by_role.setdefault(str(profile["role"]), profile)
        return by_role

    def _task_for_assignment(self, role: str, agent_tasks: list[dict[str, Any]]) -> dict[str, Any]:
        for task in agent_tasks:
            if task["role"] == role:
                return task
        return agent_tasks[0]

    def _create_team_assignments(
        self,
        *,
        project_id: str,
        loop_id: str,
        agent_tasks: list[dict[str, Any]],
        team_schedule: dict[str, Any],
    ) -> list[dict[str, Any]]:
        profiles_by_role = self._profile_by_role(project_id)
        missing_roles: list[str] = []
        missing_reviewers: list[str] = []
        for role_plan in team_schedule["roles"]:
            role = str(role_plan["role"])
            if role not in profiles_by_role:
                missing_roles.append(role)
            reviewer_role = str((role_plan.get("reviewerPolicy") or {}).get("reviewerRole") or "")
            if reviewer_role and reviewer_role not in profiles_by_role:
                missing_reviewers.append(f"{role}->{reviewer_role}")
        if missing_roles:
            raise ValueError(
                "TeamScheduler selected roles without agent profiles: "
                f"{', '.join(sorted(set(missing_roles)))}."
            )
        if missing_reviewers:
            raise ValueError(
                "TeamScheduler selected reviewer roles without agent profiles: "
                f"{', '.join(sorted(set(missing_reviewers)))}."
            )
        assignments: list[dict[str, Any]] = []
        for role_plan in team_schedule["roles"]:
            role = str(role_plan["role"])
            profile = profiles_by_role[role]
            task = self._task_for_assignment(role, agent_tasks)
            reviewer_role = str((role_plan.get("reviewerPolicy") or {}).get("reviewerRole") or "")
            reviewer = profiles_by_role.get(reviewer_role)
            assignments.append(
                self.backlog.create_agent_assignment(
                    {
                        "projectId": project_id,
                        "taskId": task["id"],
                        "agentId": profile["id"],
                        "role": role,
                        "status": "proposed",
                        "assignedBy": "team_scheduler",
                        "inputSchema": {
                            "type": "object",
                            "required": role_plan["requiredInputArtifacts"],
                            "properties": {
                                artifact: {"type": "string"}
                                for artifact in role_plan["requiredInputArtifacts"]
                            },
                            "additionalProperties": True,
                        },
                        "outputSchema": role_plan["outputArtifactSchema"],
                        "reviewRequired": role_plan["reviewer"] is not None,
                        "reviewerAgentId": reviewer["id"] if reviewer else "",
                        "metadata": {
                            "source": "team_scheduler",
                            "loopId": loop_id,
                            "schedulerVersion": team_schedule["schedulerVersion"],
                            "teamMode": team_schedule["mode"],
                            "providerPreference": role_plan["providerPreference"],
                            "runtimePreference": role_plan["runtimePreference"],
                            "resourceDecision": role_plan.get("resourceDecision") or {},
                            "qualityGates": role_plan["qualityGates"],
                            "reviewerPolicy": role_plan["reviewerPolicy"],
                        },
                    }
                )
            )
        return assignments

    def _developer_runtime_id_for_resource_selection(self, selected: dict[str, Any]) -> str | None:
        return resource_selection_phase.developer_runtime_id_for_resource_selection(self, selected)

    def _product_owner_runtime_id_for_resource_selection(self, selected: dict[str, Any]) -> str | None:
        return resource_selection_phase.product_owner_runtime_id_for_resource_selection(self, selected)

    def _product_owner_resource_selection(
        self,
        *,
        project_id: str,
        loop_id: str,
        task_id: str,
        request_meta: dict[str, Any],
        excluded_resources: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return resource_selection_phase.product_owner_resource_selection(
            self,
            project_id=project_id,
            loop_id=loop_id,
            task_id=task_id,
            request_meta=request_meta,
            excluded_resources=excluded_resources,
        )

    def _developer_execution_resource(
        self, team_schedule: dict[str, Any], request_meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return resource_selection_phase.developer_execution_resource(self, team_schedule, request_meta)

    def _developer_assignment_blocker(
        self, request_meta: dict[str, Any] | None, execution_resource: dict[str, Any]
    ) -> dict[str, Any] | None:
        return resource_selection_phase.developer_assignment_blocker(request_meta, execution_resource)

    def _security_execution_resource(
        self, team_schedule: dict[str, Any], request_meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return resource_selection_phase.security_execution_resource(self, team_schedule, request_meta)

    _ARCHITECT_TEAM_ROLES = frozenset({"architect", "software_architect"})

    def _architect_execution_resource(
        self, team_schedule: dict[str, Any], request_meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return resource_selection_phase.architect_execution_resource(self, team_schedule, request_meta)

    def _developer_execution_resource_mapping_blockers(
        self, team_schedule: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return resource_selection_phase.developer_execution_resource_mapping_blockers(self, team_schedule)

    def _record_resource_decision_learning(
        self,
        *,
        manager: AIResourceManager,
        role: str,
        decision: dict[str, Any],
        runtime_result: dict[str, Any],
        usage_entry: dict[str, Any] | None,
        evidence_ref: str,
        success: bool,
        rework: bool,
        quality_score: float,
    ) -> dict[str, Any] | None:
        return resource_learning_phase.record_resource_decision_learning(
            self,
            manager=manager,
            role=role,
            decision=decision,
            runtime_result=runtime_result,
            usage_entry=usage_entry,
            evidence_ref=evidence_ref,
            success=success,
            rework=rework,
            quality_score=quality_score,
        )

    def _record_product_owner_resource_learning(
        self,
        *,
        project_id: str,
        loop_id: str,
        resource_decision: dict[str, Any],
        product_owner_result: dict[str, Any],
        product_owner_status: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        return resource_learning_phase.record_product_owner_resource_learning(
            self,
            project_id=project_id,
            loop_id=loop_id,
            resource_decision=resource_decision,
            product_owner_result=product_owner_result,
            product_owner_status=product_owner_status,
            evidence_ref=evidence_ref,
        )

    def _record_product_owner_resource_learning_best_effort(
        self,
        *,
        project_id: str,
        loop_id: str,
        resource_decision: dict[str, Any],
        product_owner_result: dict[str, Any],
        product_owner_status: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        return resource_learning_phase.record_product_owner_resource_learning_best_effort(
            self,
            project_id=project_id,
            loop_id=loop_id,
            resource_decision=resource_decision,
            product_owner_result=product_owner_result,
            product_owner_status=product_owner_status,
            evidence_ref=evidence_ref,
        )

    def _record_resource_learning(
        self,
        *,
        project_id: str,
        loop_id: str,
        team_schedule: dict[str, Any],
        runtime_result: dict[str, Any],
        evidence_ref: str,
        success: bool,
        rework: bool,
        quality_score: float,
    ) -> dict[str, Any]:
        return resource_learning_phase.record_resource_learning(
            self,
            project_id=project_id,
            loop_id=loop_id,
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=evidence_ref,
            success=success,
            rework=rework,
            quality_score=quality_score,
        )

    def _record_resource_learning_best_effort(
        self,
        *,
        project_id: str,
        loop_id: str,
        team_schedule: dict[str, Any],
        runtime_result: dict[str, Any],
        evidence_ref: str,
        success: bool,
        rework: bool,
        quality_score: float,
    ) -> dict[str, Any]:
        return resource_learning_phase.record_resource_learning_best_effort(
            self,
            project_id=project_id,
            loop_id=loop_id,
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=evidence_ref,
            success=success,
            rework=rework,
            quality_score=quality_score,
        )

    def _attach_resource_learning_to_result(
        self,
        result: dict[str, Any],
        resource_learning: dict[str, Any],
    ) -> dict[str, Any]:
        return resource_learning_phase.attach_resource_learning_to_result(self, result, resource_learning)

    def _block_after_runtime_with_resource_learning(
        self,
        loop: dict[str, Any],
        *,
        project_id: str,
        stage: str,
        reason: str,
        actor: str,
        details: dict[str, Any],
        thread_id: str | None,
        team_schedule: dict[str, Any],
        runtime_result: dict[str, Any],
        success: bool = False,
        rework: bool = True,
        quality_score: float = 0.0,
    ) -> dict[str, Any]:
        return resource_learning_phase.block_after_runtime_with_resource_learning(
            self,
            loop,
            project_id=project_id,
            stage=stage,
            reason=reason,
            actor=actor,
            details=details,
            thread_id=thread_id,
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            success=success,
            rework=rework,
            quality_score=quality_score,
        )

    def _attach_product_owner_resource_learning_to_result(
        self,
        result: dict[str, Any],
        resource_learning: dict[str, Any],
    ) -> dict[str, Any]:
        return resource_learning_phase.attach_product_owner_resource_learning_to_result(
            self, result, resource_learning
        )

    def _requires_brief_approval(
        self, *, request_meta: dict[str, Any], result: dict[str, Any], output: dict[str, Any]
    ) -> bool:
        autonomy = request_meta.get("autonomy")
        if autonomy is None:
            autonomy = result.get("autonomy") or output.get("autonomy") or {}
        if isinstance(autonomy, str):
            return autonomy.strip().lower() in {"guided", "recommended"}
        if isinstance(autonomy, dict):
            level = str(autonomy.get("level") or autonomy.get("mode") or "").strip().lower()
            return level in {"guided", "recommended"}
        return False

    @staticmethod
    def _research_policy(request_meta: dict[str, Any]) -> dict[str, Any]:
        policy = (
            request_meta.get("researchPolicy") if isinstance(request_meta.get("researchPolicy"), dict) else {}
        )
        nested_policy = request_meta.get("policy") if isinstance(request_meta.get("policy"), dict) else {}
        nested_research = (
            nested_policy.get("research") if isinstance(nested_policy.get("research"), dict) else {}
        )
        return {**nested_research, **policy}

    def _requires_research_for_high_impact_decisions(self, request_meta: dict[str, Any]) -> bool:
        policy = self._research_policy(request_meta)
        value = (
            policy.get("requireForHighImpactTechnicalDecisions")
            if "requireForHighImpactTechnicalDecisions" in policy
            else policy.get("requireResearchForHighImpactTechnicalDecisions")
        )
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "required", "require"}

    @staticmethod
    def _is_high_impact_technical_decision(decision: dict[str, Any]) -> bool:
        category = str(decision.get("category") or decision.get("type") or "").strip().lower()
        impact = (
            str(
                decision.get("impact")
                or decision.get("risk")
                or decision.get("severity")
                or decision.get("priority")
                or ""
            )
            .strip()
            .lower()
        )
        text = " ".join(
            str(decision.get(key) or "")
            for key in ("title", "question", "decision", "recommendation", "rationale")
        ).lower()
        technical = category in TECHNICAL_DECISION_CATEGORIES or any(
            marker in text
            for marker in (
                "architecture",
                "database",
                "migration",
                "security",
                "runtime",
                "api",
                "schema",
                "infrastructure",
            )
        )
        high_impact = impact in HIGH_IMPACT_RESEARCH_VALUES or bool(decision.get("requiresResearch"))
        return technical and high_impact

    def _high_impact_technical_decisions_requiring_research(
        self, *, request_meta: dict[str, Any], output: dict[str, Any]
    ) -> list[dict[str, Any]]:
        decisions = [item for item in output.get("decisions") or [] if isinstance(item, dict)]
        if not decisions:
            return []
        policy_requires = self._requires_research_for_high_impact_decisions(request_meta)
        return [
            decision
            for decision in decisions
            if (policy_requires or bool(decision.get("requiresResearch")))
            and self._is_high_impact_technical_decision(decision)
        ]

    def _queue_required_research(
        self,
        *,
        project_id: str,
        thread_id: str,
        message_id: str,
        workspace_id: str,
        loop_id: str,
        message: str,
        request_meta: dict[str, Any],
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        policy = self._research_policy(request_meta)
        decision_titles = [
            str(
                decision.get("title") or decision.get("decision") or decision.get("recommendation") or ""
            ).strip()
            for decision in decisions
        ]
        query = (
            "Research official sources before accepting high-impact technical decisions: "
            + "; ".join(title for title in decision_titles if title)
        ).strip()
        if not query.endswith("."):
            query = f"{query}."
        payload = {
            "threadId": thread_id,
            "messageId": message_id,
            "projectId": project_id,
            "workspaceId": workspace_id,
            "taskId": f"product-loop-research-{stable_task_suffix(thread_id, loop_id)}",
            "query": f"{query} Original request: {message}",
            "maxSources": policy.get("maxSources") or 5,
            "sources": [],
            "conclusions": [],
            "claims": [],
            "technicalDecisions": [],
            "root": str(self.root) if self.root is not None else None,
            "metadata": {
                "threadId": thread_id,
                "messageId": message_id,
                "loopId": loop_id,
                "allowWebSearch": bool(policy.get("allowWebSearch") or policy.get("webSearchAllowed")),
                "researchPolicy": policy,
                "requiredFor": "high_impact_technical_decision",
                "decisions": decisions,
            },
        }
        job = self.jobs.create_job(
            project_id=project_id,
            kind=THREAD_RESEARCH_JOB_KIND,
            payload=payload,
            idempotency_key=f"product-loop-research:{loop_id}:{thread_id}",
        )["job"]
        self._record_thread_event(
            thread_id=thread_id,
            event_type="research_required",
            agent_role="researcher",
            payload={
                "loopId": loop_id,
                "jobId": job["id"],
                "reason": "High-impact technical decisions require ResearchAgent evidence by policy.",
                "decisionCount": len(decisions),
            },
        )
        return job

    def _create_brief_approval(
        self, *, project_id: str, loop_id: str, brief: dict[str, Any], artifact_ids: list[str]
    ) -> dict[str, Any]:
        job = self.jobs.create_job(
            project_id=project_id,
            kind="product_loop_brief_approval",
            status="approval_required",
            payload={"loopId": loop_id, "briefId": brief["id"], "evidenceRefs": artifact_ids},
        )["job"]
        action = self.jobs.create_action_request(
            job_id=job["id"],
            project_id=project_id,
            action_type="product_loop.approve_brief",
            risk_level="medium",
            command="approve product brief",
            payload={"loopId": loop_id, "briefId": brief["id"], "evidenceRefs": artifact_ids},
            reason="Review ProductOwnerAgent brief before backlog generation.",
        )
        return {
            "status": "approval_required",
            "jobId": job["id"],
            "actionRequestId": action["id"],
            "briefId": brief["id"],
            "evidenceRefs": artifact_ids,
        }

    def _cancel_run(self, loop_id: str, *, actor: str, thread_id: str | None) -> dict[str, Any]:
        """Lleva al estado terminal `cancelled` un loop abortado en vuelo por el operador."""
        reason = "Execution cancelled by the operator."
        loop = self.repository.get_loop(loop_id)
        cancelled = self._transition_run_state(
            loop,
            to_state=CANCELLED_STATE,
            reason=reason,
            trigger="operator_cancelled",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop, {"status": CANCELLED_STATE, "cancelledReason": reason}
            ),
            thread_id=thread_id,
        )
        return self._run_result(cancelled, status=CANCELLED_STATE, reason=reason)

    def run_user_message(
        self,
        *,
        project_id: str,
        message: str,
        root: str | Path | None = None,
        title: str | None = None,
        preferred_runtime: str | None = None,
        qa_commands: list[Any] | None = None,
        run_metadata: dict[str, Any] | None = None,
        actor: str = "operator",
        session_id: str | None = None,
        thread_id: str | None = None,
        runtime_runner: Any | None = None,
        git_service: Any | None = None,
        product_owner_runner: Any | None = None,
        assessment_runner: Any | None = None,
        technical_lead_runner: Any | None = None,
        security_runner: Any | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Run one user message through the durable Product Loop control plane.

        `should_abort` is polled at every stage boundary: when it reports that the operator cancelled the
        run, the loop stops there and settles in the terminal `cancelled` state instead of finishing
        alongside a replacement run.
        """
        self._should_abort = should_abort
        try:
            return self._run_user_message(
                project_id=project_id,
                message=message,
                root=root,
                title=title,
                preferred_runtime=preferred_runtime,
                qa_commands=qa_commands,
                run_metadata=run_metadata,
                actor=actor,
                session_id=session_id,
                thread_id=thread_id,
                runtime_runner=runtime_runner,
                git_service=git_service,
                product_owner_runner=product_owner_runner,
                assessment_runner=assessment_runner,
                technical_lead_runner=technical_lead_runner,
                security_runner=security_runner,
            )
        except _ProductLoopCancelled as cancelled:
            return self._cancel_run(cancelled.loop_id, actor=actor, thread_id=cancelled.thread_id)
        finally:
            self._should_abort = None

    def _run_user_message(
        self,
        *,
        project_id: str,
        message: str,
        root: str | Path | None = None,
        title: str | None = None,
        preferred_runtime: str | None = None,
        qa_commands: list[Any] | None = None,
        run_metadata: dict[str, Any] | None = None,
        actor: str = "operator",
        session_id: str | None = None,
        thread_id: str | None = None,
        runtime_runner: Any | None = None,
        git_service: Any | None = None,
        product_owner_runner: Any | None = None,
        assessment_runner: Any | None = None,
        technical_lead_runner: Any | None = None,
        security_runner: Any | None = None,
    ) -> dict[str, Any]:
        """Ejecuta el mensaje del usuario en el control plane durable, síncrono y fail-closed.

        Cada mensaje crea o reutiliza un thread/mensaje persistido y termina en un resultado real de
        pregunta/brief/backlog/ejecución/rework/bloqueo/aprobación. Las dependencias de agentes, runtime
        y git son inyectables por tests; producción usa los runners reales.
        """
        run = _UserMessageRun(
            project_id=project_id,
            message=message,
            actor=actor,
            root=root,
            title=title,
            preferred_runtime=preferred_runtime,
            qa_commands=qa_commands,
            run_metadata=run_metadata,
            session_id=session_id,
            thread_id=thread_id,
            runtime_runner=runtime_runner,
            git_service=git_service,
            product_owner_runner=product_owner_runner,
            assessment_runner=assessment_runner,
            technical_lead_runner=technical_lead_runner,
            security_runner=security_runner,
        )
        risk_continuation = bool((run_metadata or {}).get("runtimeRiskContinuation"))
        research_continuation = bool((run_metadata or {}).get("researchContinuation"))
        if risk_continuation:
            from local_control_center.product_loop.runtime_risk_review import load_runtime_risk_continuation

            try:
                load_runtime_risk_continuation(self, run)
            except (KeyError, ValueError, TypeError, OSError) as error:
                marker = (run_metadata or {}).get("runtimeRiskContinuation") or {}
                with suppress(KeyError):
                    run.loop = self.get(str(marker.get("loopId") or ""))
                return {"status": "blocked", "reason": str(error), "loop": run.loop}
        result = (
            None
            if risk_continuation
            else self._load_research_continuation(run)
            if research_continuation
            else self._ensure_thread_and_similarity(run)
        )
        if result is not None:
            return result
        self._seal_constitution(run)
        result = self._check_workspace_and_git(run)
        if result is not None:
            return result
        if research_continuation or risk_continuation:
            result = self._revalidate_research_continuation(run) if research_continuation else None
            if result is not None:
                return result
            run.loop = self._transition_run_state(
                run.loop,
                to_state="discovery",
                reason="Continuing the verified persisted ProductOwner output.",
                trigger="research_continuation",
                actor=run.actor,
                thread_id=run.thread_id,
                context_patch=self._durable_run_patch(
                    run.loop, {"blockedStage": None, "blockedReason": None}
                ),
            )
        else:
            result = self._select_product_owner_resources(run)
            if result is not None:
                return result
            result = self._run_project_assessment(run)
            if result is not None:
                return result
            result = self._run_discovery_phase(run)
            if result is not None:
                return result
            result = self._persist_product_owner_results(run)
            if result is not None:
                return result
        if risk_continuation:
            from local_control_center.product_loop.runtime_risk_review import resume_runtime_risk_planning

            try:
                result = resume_runtime_risk_planning(self, run)
            except (KeyError, ValueError, TypeError, OSError) as error:
                return self._block_run(
                    run.loop,
                    stage="resource_manager",
                    reason=str(error),
                    actor=run.actor,
                    thread_id=run.thread_id,
                    details={"runtimeRiskReviewInvalid": True},
                )
        else:
            result = self._route_product_owner_outcome(run)
            if result is not None:
                return result
            result = self._plan_team_and_resources(run)
        if result is not None:
            return result
        result = self._prepare_developer_execution(run)
        if result is not None:
            return result
        run.base_task_id = run.task_id
        result = story_loop_phase.run_story_batches(self, run)
        if result is not None:
            return result
        result = self._capture_cumulative_review(run)
        if result is not None:
            return result
        result = self._run_security_phase(run)
        if result is not None:
            return result
        self._run_analysis_phase(run)
        self._run_team_review_phase(run)
        return self._finalize_delivery_approval(run)

    def _evaluate_qa_gate(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return qa_gate_phase.evaluate_qa_gate(self, run)

    def _run_security_phase(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return security_phase.run_security_phase(self, run)

    def _finalize_delivery_approval(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return approval_phase.finalize_delivery_approval(self, run)

    def _render_spec_artifacts(
        self, run: _UserMessageRun, workspace: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Materializa constitution/spec/tasks como Markdown generado dentro del worktree del run.

        Best-effort: los archivos son renders (la BD es la fuente de verdad) y quedan excluidos del
        guard de no-op via ``exclude_aido_artifacts``, asi que un fallo aqui jamas bloquea el run ni
        un exito puede convertir un run vacio en trabajo aparente.
        """
        try:
            project_id = run.project_id
            stories = self.backlog.list_user_stories(project_id=project_id)
            criteria = self.backlog.list_acceptance_criteria(project_id=project_id)
            result = write_spec_artifacts(
                workspace["path"],
                thread_id=str(run.thread_id or ""),
                title=run.resolved_title or "Product loop",
                constitution=run.constitution or None,
                brief=run.brief or None,
                stories=stories,
                acceptance_criteria=criteria,
                tasks=run.agent_tasks,
                task_dependencies=self.backlog.list_task_dependencies(project_id=project_id),
                technical_plan=run.technical_plan or None,
            )
            return result if result.get("files") else None
        except Exception as error:
            self._record_loop_event(
                project_id=run.project_id,
                event_type="product_loop.spec_artifacts_unavailable",
                loop_id=str((run.loop or {}).get("id") or ""),
                payload={"reason": redact_secrets(str(error))},
                thread_id=run.thread_id,
            )
            return None

    def _run_plan_phase(
        self,
        run: _UserMessageRun,
        *,
        loop: dict[str, Any],
        raw_plan: dict[str, Any],
        team_schedule: dict[str, Any],
        agent_tasks: list[dict[str, Any]],
        actor: str,
        thread_id: str | None,
    ) -> dict[str, Any]:
        """Fase Plan: emite architecture_review con el plan tecnico sellado, o el carril rapido.

        Con risk low e intents de mantenimiento (docs/tests/cleanup/bugfix) se salta la fase dejando
        specDriven=skipped_low_risk en el contexto durable; en el resto de los casos persiste la
        vista del plan (quality gates agregados, handoffs, estrategia branch/worktree) como artefacto
        technical_plan y transiciona planning -> architecture_review. Best-effort: un fallo deja
        evento y el run sigue al backlog, porque el plan es gobierno del proceso, no gate de entrega.
        """
        try:
            intent = (team_schedule or {}).get("intent") or {}
            decision = plan_phase_decision(intent)
            if decision == SKIPPED_LOW_RISK:
                durable = self._durable_run_context(loop)
                durable["specDriven"] = {"plan": SKIPPED_LOW_RISK, "risk": intent.get("risk")}
                durable["updatedAt"] = utc_now()
                return self._write_context_entries(loop, {"durableRun": durable})
            technical_plan = technical_plan_view(raw_plan, agent_tasks=agent_tasks)
            if technical_plan is None:
                return loop
            artifact = self._write_json_artifact(
                root=run.effective_root or self.root,
                project_id=run.project_id,
                name="technical-plan",
                kind="technical_plan",
                payload=technical_plan,
            )
            run.technical_plan = technical_plan
            return self._transition_run_state(
                loop,
                to_state="architecture_review",
                reason="TechnicalLead plan (gates, handoffs, workspace strategy) is persisted for review.",
                trigger="technical_plan_ready",
                actor=actor,
                context_patch=self._durable_run_patch(
                    loop,
                    {
                        "status": "architecture_review",
                        "technicalPlan": {**technical_plan, "artifactId": artifact["id"]},
                        "specDriven": {"plan": PLANNED, "risk": intent.get("risk")},
                    },
                ),
                thread_id=thread_id,
            )
        except _ProductLoopCancelled:
            raise
        except Exception as error:
            self._record_loop_event(
                project_id=run.project_id,
                event_type="product_loop.plan_phase_unavailable",
                loop_id=str(loop.get("id") or ""),
                payload={"reason": redact_secrets(str(error))},
                thread_id=thread_id,
            )
            return loop

    def _run_analysis_phase(self, run: _UserMessageRun) -> None:
        """Fase Análisis: emite quality_review con la consistencia spec/tareas/diff como evidencia.

        Advisory por diseño: los hallazgos quedan visibles para la aprobación humana pero el
        veredicto de entrega sigue siendo de QA/Security. El carril rápido (specDriven
        skipped_low_risk) la salta, y un fallo interno deja evento sin bloquear el run.
        """
        try:
            loop = run.loop
            durable = self._durable_run_context(loop)
            spec_driven = durable.get("specDriven") or {}
            if str(spec_driven.get("plan") or "") == SKIPPED_LOW_RISK:
                return
            analysis = analyze_run_consistency(
                stories=self.backlog.list_user_stories(project_id=run.project_id),
                acceptance_criteria=self.backlog.list_acceptance_criteria(project_id=run.project_id),
                agent_tasks=run.agent_tasks,
                changed_files=[str(item) for item in (run.review or {}).get("changedFiles") or []],
            )
            evidence = self._record_run_evidence(
                project_id=run.project_id,
                loop_id=loop["id"],
                stage="analysis",
                status="completed",
                reason=(
                    "Spec/tasks/diff consistency analysis found no gaps."
                    if analysis["status"] == "consistent"
                    else f"Spec/tasks/diff consistency analysis reported {len(analysis['findings'])} finding(s)."
                ),
                details=analysis,
                workspace_id=(run.workspace or {}).get("id"),
            )
            run.evidence_ids.append(evidence["id"])
            run.loop = self._transition_run_state(
                loop,
                to_state="quality_review",
                reason="Cross-checking the delivered diff against the spec and planned tasks.",
                trigger="analysis",
                actor=run.actor,
                context_patch=self._durable_run_patch(
                    loop,
                    {"status": "quality_review", "analysis": analysis},
                    evidence_package_ids=[evidence["id"]],
                ),
                thread_id=run.thread_id,
            )
        except _ProductLoopCancelled:
            raise
        except Exception as error:
            self._record_loop_event(
                project_id=run.project_id,
                event_type="product_loop.analysis_phase_unavailable",
                loop_id=str((run.loop or {}).get("id") or ""),
                payload={"reason": redact_secrets(str(error))},
                thread_id=run.thread_id,
            )

    _ARCHITECT_REVIEW_INTENTS = frozenset({"architecture", "refactor", "migration"})
    """Intents/riesgos que ameritan revision de arquitectura post-diff (misma regla que _gates_for)."""
    _ARCHITECT_REVIEW_RISKS = frozenset({"high", "critical"})

    def _run_team_review_phase(self, run: _UserMessageRun) -> None:
        """Revisiones advisory del equipo extendido sobre el diff ya validado por QA/Security.

        El ArchitectAgent corre solo cuando el intent (architecture/refactor/migration) o el riesgo
        (high/critical) lo ameritan — donde su contrato post-diff funciona, alimentado con el
        patchArtifactId real (el diff acumulado del run cuando existe). El DevOpsAgent corre solo
        con project.quality.devopsChecksEnabled y usa project.quality.gateCommands como quality
        scripts (reactivando el setting huerfano).
        Ambos son advisory: sus veredictos quedan en el contexto durable y la evidencia para la
        aprobacion humana, replicando la asimetria de issue_to_pr donde el architect no bloquea; un
        fallo de esta fase deja evento y el run continua.
        """
        try:
            loop = run.loop
            intent = (run.team_schedule or {}).get("intent") or {}
            intents = {str(item).strip().lower() for item in intent.get("intents") or []}
            risk = str(intent.get("risk") or "").strip().lower()
            reviews: dict[str, Any] = {}
            patch_artifact_id = getattr(run, "cumulative_patch_artifact_id", "") or str(
                ((run.runtime_result or {}).get("diffSummary") or {}).get("patchArtifactId") or ""
            )
            architect_runtime = assigned_runtime(run.request_meta, "architect")
            architect_unassigned = runtime_team_of(run.request_meta) is not None and not architect_runtime
            if patch_artifact_id and (
                intents & self._ARCHITECT_REVIEW_INTENTS or risk in self._ARCHITECT_REVIEW_RISKS
            ):
                if architect_unassigned:
                    discarded = discarded_role_runtime(run.request_meta, "architect")
                    reviews["architect"] = {
                        "status": "skipped",
                        "verdict": "",
                        "reason": (
                            f"The architect runtime {discarded['providerId']} was discarded when the run "
                            f"was sealed ({discarded.get('reason') or discarded.get('status')})."
                            if discarded
                            else "The thread runtime team assigns no architect runtime."
                        ),
                        "evidencePackageId": "",
                    }
                else:
                    try:
                        architect_payload: dict[str, Any] = {
                            "projectId": run.project_id,
                            "workspaceId": run.workspace["id"],
                            "taskId": f"{run.task_id}.architect",
                            "diffArtifactId": patch_artifact_id,
                            "workflowContext": {
                                "workflowKind": "product_loop",
                                "attempt": run.rework_round + 1,
                                "title": run.resolved_title,
                            },
                            "testResults": run.qa_results,
                            "constitution": render_constitution_prompt(run.constitution) or None,
                        }
                        architect_payload.update(
                            self._architect_execution_resource(run.team_schedule, run.request_meta)
                        )
                        architect = ArchitectAgentRunner(self.connection, root=run.effective_root).run(
                            architect_payload
                        )
                        reviews["architect"] = {
                            "status": str(architect.get("status") or ""),
                            "verdict": str(architect.get("verdict") or ""),
                            "reason": str(architect.get("reason") or ""),
                            "evidencePackageId": str(
                                (architect.get("evidencePackage") or {}).get("id") or ""
                            ),
                        }
                        if architect.get("localRuntimeCause"):
                            reviews["architect"]["localRuntimeCause"] = str(architect["localRuntimeCause"])
                    except Exception as error:
                        reviews["architect"] = {"status": "failed", "reason": redact_secrets(str(error))}
            devops_enabled = bool(
                resolve_setting_value(
                    connection=self.connection,
                    key="project.quality.devopsChecksEnabled",
                    project_id=run.project_id,
                )
            )
            if devops_enabled:
                gate_commands = resolve_setting_value(
                    connection=self.connection,
                    key="project.quality.gateCommands",
                    project_id=run.project_id,
                )
                try:
                    devops = DevOpsAgentRunner(self.connection, root=run.effective_root).run(
                        {
                            "projectId": run.project_id,
                            "workspaceId": run.workspace["id"],
                            "taskId": f"{run.task_id}.devops",
                            "buildScripts": [],
                            "qualityScripts": iteration_scripts(
                                [str(item) for item in (gate_commands or []) if str(item).strip()],
                                tier="story",
                            ),
                            "dockerHealthcheck": False,
                        }
                    )
                    reviews["devops"] = {
                        "qualityTier": "story",
                        "fullDeliveryGateVerified": False,
                        "status": str(devops.get("status") or ""),
                        "verdict": str(devops.get("verdict") or ""),
                        "reason": str(devops.get("reason") or ""),
                        "evidencePackageId": str((devops.get("evidencePackage") or {}).get("id") or ""),
                    }
                except Exception as error:
                    reviews["devops"] = {"status": "failed", "reason": redact_secrets(str(error))}
            if not reviews:
                return
            durable = self._durable_run_context(loop)
            durable["teamReviews"] = redact_secrets(reviews)
            durable["updatedAt"] = utc_now()
            run.loop = self._write_context_entries(loop, {"durableRun": durable})
            self._record_loop_event(
                project_id=run.project_id,
                event_type="product_loop.team_reviews_recorded",
                loop_id=str(loop.get("id") or ""),
                payload={
                    role: {"status": review.get("status"), "verdict": review.get("verdict")}
                    for role, review in reviews.items()
                },
                thread_id=run.thread_id,
            )
        except _ProductLoopCancelled:
            raise
        except Exception as error:
            self._record_loop_event(
                project_id=run.project_id,
                event_type="product_loop.team_review_unavailable",
                loop_id=str((run.loop or {}).get("id") or ""),
                payload={"reason": redact_secrets(str(error))},
                thread_id=run.thread_id,
            )

    def _load_research_continuation(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Consume only the server-created job and durable receipt, before any fresh intake or inference."""
        from local_control_center.process_supervision.context import CURRENT_EXECUTION
        from local_control_center.product_loop.research_resolution import (
            ResearchResolutionError,
            hydrate_research_run,
            resolution_snapshot,
            validate_research,
        )

        loop: dict[str, Any] = {}
        try:
            metadata = run.run_metadata or {}
            marker = metadata.get("researchContinuation")
            context = CURRENT_EXECUTION.get()
            if (
                not isinstance(marker, dict)
                or context is None
                or not context.in_job_runner
                or context.connection is not self.connection
                or context.project_id != run.project_id
                or not context.execution_id
                or context.execution_id != metadata.get("jobId")
            ):
                raise ResearchResolutionError("Research continuation requires its admitted worker execution.")
            with immediate_transaction(self.connection):
                job = self.jobs.get_job(context.execution_id)
                if (
                    job["kind"] != "thread.product_loop.run"
                    or job["status"] != "running"
                    or job["projectId"] != run.project_id
                    or job["payload"].get("threadId") != run.thread_id
                    or (job["payload"].get("runMetadata") or {}).get("researchContinuation") != marker
                ):
                    raise ResearchResolutionError(
                        "The worker job does not authorize this research continuation."
                    )
                loop = self.repository.get_loop(str(marker.get("loopId") or ""))
                if self.connection.execute(
                    "SELECT 1 FROM product_loops WHERE project_id=? AND rowid>(SELECT rowid FROM product_loops WHERE id=?) "
                    "AND json_extract(context,'$.durableRun.thread.projectThreadId')=? LIMIT 1",
                    (run.project_id, loop["id"], run.thread_id),
                ).fetchone():
                    raise ResearchResolutionError(
                        "A newer Product Loop superseded this research continuation."
                    )
                durable = dict(loop["context"].get("durableRun") or {})
                receipt = dict(durable.get("researchResolution") or {})
                action = RemediationActionsRepository(self.connection).get(
                    str(receipt.get("remediationActionId") or "")
                )
                if (
                    loop["projectId"] != run.project_id
                    or loop["state"] != "blocked"
                    or durable.get("blockedStage") != "research"
                    or durable.get("runActive")
                    or receipt.get("status") != "queued"
                    or receipt.get("id") != marker.get("receiptId")
                    or receipt.get("jobId") != job["id"]
                    or receipt.get("loopVersion") != loop["version"]
                    or action["status"] != "resolved"
                    or action["loopId"] != loop["id"]
                    or action["threadId"] != run.thread_id
                    or action["actionType"] != "retry_loop"
                ):
                    raise ResearchResolutionError(
                        "The research receipt is stale, consumed, or belongs to another loop."
                    )
                snapshot = resolution_snapshot(self.connection, loop)
                if (
                    snapshot != receipt.get("snapshot")
                    or snapshot["threadId"] != run.thread_id
                    or str(run.message).strip() != str(durable["message"]).strip()
                ):
                    raise ResearchResolutionError(
                        "Research decisions or scope changed while the continuation was queued."
                    )
                validated = validate_research(
                    self.connection,
                    root=self.root,
                    snapshot=snapshot,
                    research_run_id=receipt["researchRunId"],
                )
                if any(receipt.get(key) != value for key, value in validated.items()):
                    raise ResearchResolutionError(
                        "Research evidence changed while the continuation was queued."
                    )
                receipt.update({"status": "consumed", "consumedAt": utc_now()})
                durable.update({"researchResolution": receipt, "runActive": True})
                loop = self._write_context_entries(loop, {"durableRun": durable})
                hydrate_research_run(self, run, loop=loop, receipt=receipt)
            return None
        except (ResearchResolutionError, KeyError, OSError, ValueError, TypeError) as error:
            return {"status": "blocked", "reason": str(error), "loop": loop}

    def _revalidate_research_continuation(self, run: _UserMessageRun) -> dict[str, Any] | None:
        from local_control_center.product_loop.research_resolution import (
            ResearchResolutionError,
            resolution_snapshot,
            validate_research,
        )

        try:
            loop = self.repository.get_loop(run.loop["id"])
            snapshot = resolution_snapshot(self.connection, loop)
            if snapshot != run.research_resolution["snapshot"]:
                raise ResearchResolutionError("Research decisions changed during the workspace checks.")
            validated = validate_research(
                self.connection,
                root=self.root,
                snapshot=snapshot,
                research_run_id=run.research_resolution["researchRunId"],
            )
            if any(run.research_resolution.get(key) != value for key, value in validated.items()):
                raise ResearchResolutionError("Research evidence changed during the workspace checks.")
            run.loop = loop
            return None
        except (ResearchResolutionError, KeyError, OSError, ValueError, TypeError) as error:
            return self._block_run(
                run.loop,
                stage="research",
                reason=str(error),
                actor=run.actor,
                details=self._durable_run_context(run.loop).get("research") or {},
                thread_id=run.thread_id,
            )

    def _clear_run_active(self, loop: dict[str, Any]) -> dict[str, Any]:
        """Apaga el marcador durable ``runActive`` en todo cierre controlado del run.

        Un ``runActive`` que sobrevive es la firma de un run interrumpido (worker muerto o
        excepcion no controlada): el proximo run del hilo lo detecta y supersede el loop zombi.
        Best-effort: un fallo aqui no puede romper el resultado que ya esta construido.
        """
        try:
            if not isinstance(loop, dict) or not loop.get("id"):
                return loop
            durable = self._durable_run_context(loop)
            if not durable.get("runActive"):
                return loop
            durable["runActive"] = False
            durable["updatedAt"] = utc_now()
            return self._write_context_entries(loop, {"durableRun": durable})
        except Exception:
            return loop

    def _supersede_continued_loop(
        self, *, project_id: str, thread_id: str, request_meta: dict[str, Any], actor: str
    ) -> None:
        """Cierra el loop que este run de continuación reemplaza: quedaba ``reworking`` para siempre.

        Rechazar una entrega deja el loop en ``reworking`` y encola una continuación que corre en un
        loop nuevo; nada cerraba el original y el hilo acumulaba loops "en rework" sin nada en marcha
        (visto en vivo: tres en el hilo del sandbox). Solo el loop de ``continueOfLoopId`` con la
        procedencia de su feedback ``continue``, del mismo proyecto e hilo, todavía en ``reworking`` y
        sin run activo. Best-effort, igual que el supersede de runs interrumpidos.
        """
        source_id = str(request_meta.get("continueOfLoopId") or "").strip()
        if not source_id:
            return
        try:
            source = self.repository.get_loop(source_id)
        except KeyError:
            return
        durable = self._durable_run_context(source)
        thread = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
        if (
            source["projectId"] != project_id
            or str(thread.get("projectThreadId") or "") != str(thread_id)
            or source["state"] != REWORK_STATE
            or durable.get("runActive")
            or not self._trusted_feedback_resource_approval_marker(
                request_meta=request_meta, source_loop=source
            )
        ):
            return
        try:
            self.transition(
                source_id,
                to_state=CANCELLED_STATE,
                reason="Superseded by the continuation run of its delivery feedback.",
                trigger="superseded_by_continuation",
                actor=actor,
            )
        except (ProductLoopTransitionError, ProductLoopStopConditionError):
            return

    def _supersede_interrupted_loops(self, *, project_id: str, thread_id: str, actor: str) -> None:
        """Cancela los loops del hilo que quedaron a mitad de ejecucion (re-entry durable).

        Un loop no terminal con ``runActive=True`` es un run interrumpido: su worker murio o el
        proceso cayo antes del cierre controlado. Dejarlo vivo confunde la UI para siempre y puede
        vetar la resolucion de decisiones (guard fail-closed sobre loops en ``awaiting_user``).
        Cancelarlo al arrancar el run siguiente resuelve ademas sus remediaciones pendientes
        (``transition`` resuelve al llegar a terminal). Best-effort: un fallo deja evento y el run
        continua.

        Doble guard, no uno: ademas de ``runActive`` (que el cierre controlado apaga via
        ``_clear_run_active``) se exige que el estado sea genuinamente de ejecucion en curso. Los
        estados parqueados esperando al operador (``_OPERATOR_WAIT_STATES``) nunca se supersedan
        aunque su ``runActive`` haya quedado colgado por un ``_clear_run_active`` best-effort fallido
        (p. ej. 'database is locked' bajo contencion): un run que entrego control al humano jamas es
        un run interrumpido, y cancelarlo descartaria trabajo entregado o una remediacion en curso.
        """
        try:
            for loop in self.repository.list_loops(project_id):
                if loop.get("state") in TERMINAL_STATES or loop.get("state") in _OPERATOR_WAIT_STATES:
                    continue
                fsm = (loop.get("context") or {}).get("fsm") or {}
                if str(fsm.get("correlationId") or "") != str(thread_id):
                    continue
                durable = self._durable_run_context(loop)
                if not durable.get("runActive"):
                    continue
                try:
                    self.transition(
                        loop["id"],
                        to_state=CANCELLED_STATE,
                        reason="Superseded: the previous run was interrupted before finishing.",
                        trigger="superseded_interrupted",
                        actor=actor,
                    )
                except (ProductLoopTransitionError, ProductLoopStopConditionError):
                    continue
        except Exception as error:
            self._record_loop_event(
                project_id=project_id,
                event_type="product_loop.supersede_unavailable",
                loop_id="",
                payload={"reason": redact_secrets(str(error))},
                thread_id=thread_id,
            )

    def _seal_constitution(self, run: _UserMessageRun) -> None:
        """Garantiza una constitución vigente y sella su versión + hash en el contexto durable del run.

        Best-effort por diseño: la constitución bootstrapped es ``advisory`` y un fallo aquí jamás
        bloquea el run (el gobierno duro solo lo activa el operador con ``enforcement='enforced'``).
        El sello deja auditado con qué versión exacta del documento corrió cada run, siguiendo el
        patrón de ``seal_operator_cost_decision``.
        """
        try:
            repo = ProjectConstitutionRepository(self.connection)
            constitution = repo.get_for_project(run.project_id)
            if constitution is None:
                goal_statement = str(
                    resolve_setting_value(
                        connection=self.connection,
                        key="project.goal.statement",
                        project_id=run.project_id,
                    )
                    or ""
                ).strip()
                with immediate_transaction(self.connection):
                    constitution = repo.bootstrap_if_missing(run.project_id, goal_statement=goal_statement)
            run.constitution = constitution
            loop = run.loop
            if not isinstance(loop, dict) or not loop.get("id"):
                return
            durable = self._durable_run_context(loop)
            durable["constitution"] = {
                "constitutionId": constitution["id"],
                "version": constitution["version"],
                "contentHash": constitution["contentHash"],
                "source": constitution["source"],
                "enforcement": constitution["enforcement"],
            }
            durable["updatedAt"] = utc_now()
            run.loop = self._write_context_entries(loop, {"durableRun": durable})
        except Exception as error:
            self._record_loop_event(
                project_id=run.project_id,
                event_type="product_loop.constitution_unavailable",
                loop_id=str((run.loop or {}).get("id") or ""),
                payload={"reason": redact_secrets(str(error))},
                thread_id=run.thread_id,
            )

    def _ensure_thread_and_similarity(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return intake_phase.ensure_thread_and_similarity(self, run)

    def _check_workspace_and_git(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return environment_phase.check_workspace_and_git(self, run)

    def _select_product_owner_resources(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return environment_phase.select_product_owner_resources(self, run)

    def _run_project_assessment(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return environment_phase.run_project_assessment(self, run)

    def _run_discovery_phase(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return discovery_phase.run_discovery_phase(self, run)

    def _persist_product_owner_results(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return discovery_phase.persist_product_owner_results(self, run)

    def _route_product_owner_outcome(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return discovery_phase.route_product_owner_outcome(self, run)

    def _plan_team_and_resources(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return team_planning_phase.plan_team_and_resources(self, run)

    def _work_branch_prefix(self, project_id: str) -> str:
        """Prefijo configurable de las ramas de trabajo (``project.git.workBranchPrefix``).

        Se sanitiza como segmento de rama seguro; vacío o inseguro cae al default ``codex``.
        """
        from local_control_center.workspaces_projects.git_worktrees import slugify_branch_segment

        raw = str(
            resolve_setting_value(
                connection=self.connection, key="project.git.workBranchPrefix", project_id=project_id
            )
            or "codex"
        ).strip()
        return slugify_branch_segment(raw) if raw else "codex"

    def _resolve_project_base_branch(self, project_id: str) -> str:
        """Rama base del proyecto (``project.git.baseBranch``) sobre la que se abren los worktrees.

        Por defecto ``dev`` (modelo equipo-real). ``create_git_worktree`` cae a ``HEAD`` si la rama
        configurada no existe en el repo, así que un proyecto sin ``dev`` nunca queda sin aislar.
        """
        value = resolve_setting_value(
            connection=self.connection, key="project.git.baseBranch", project_id=project_id
        )
        return str(value or "dev").strip() or "dev"

    def _prepare_developer_execution(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return execution_phase.prepare_developer_execution(self, run)

    def _failover_replacement(
        self,
        *,
        run: _UserMessageRun,
        payload: dict[str, Any],
        attempts: list[dict[str, Any]],
        provider_id: str,
        failed_model: str,
        role: str,
    ) -> dict[str, Any] | None:
        return failover_phase.failover_replacement(
            self,
            run=run,
            payload=payload,
            attempts=attempts,
            provider_id=provider_id,
            failed_model=failed_model,
            role=role,
        )

    def _raise_if_usage_suspended(self, payload: dict[str, Any]) -> None:
        """Corta el intento si su proveedor quedó sobre el umbral de uso del operador."""
        failover_phase.raise_if_usage_suspended(getattr(self, "connection", None), payload)

    def _run_with_failover(
        self,
        *,
        runtime: Any,
        payload: dict[str, Any],
        run: _UserMessageRun,
        attempts: list[dict[str, Any]],
        thread_id: str | None,
        role: str = "developer",
    ) -> dict[str, Any]:
        return failover_phase.run_with_failover(
            self, runtime=runtime, payload=payload, run=run, attempts=attempts, thread_id=thread_id, role=role
        )

    def _execute_developer_phase(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return execution_phase.execute_developer_phase(self, run)

    def _capture_review_evidence(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return execution_phase.capture_review_evidence(self, run)

    def _capture_cumulative_review(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return execution_phase.capture_cumulative_review(self, run)

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
        """Crea un loop en ``goal_received`` con su política de gobierno y registra la transición inicial."""
        return self.loop_fsm.start(
            project_id=project_id,
            title=title,
            initiative_id=initiative_id,
            context=context,
            correlation_id=correlation_id,
            budget=budget,
            timeouts=timeouts,
            max_rework_rounds=max_rework_rounds,
            deadline=deadline,
            actor=actor,
            reason=reason,
            now=now,
        )

    def get(self, loop_id: str) -> dict[str, Any]:
        """Lee el loop durable por id."""
        return self.loop_fsm.get(loop_id)

    def list_loops(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista loops (todos o por proyecto), el más recientemente actualizado primero."""
        return self.loop_fsm.list_loops(project_id)

    def list_transitions(self, loop_id: str) -> list[dict[str, Any]]:
        """Devuelve la bitácora de transiciones del loop en orden cronológico."""
        return self.loop_fsm.list_transitions(loop_id)

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
        """Valida y aplica una transición durable; persiste estado, razón, correlation id y timestamp."""
        return self.loop_fsm.transition(
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
        return self.loop_fsm.transition_in_transaction(
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
        return self.loop_fsm._apply_transition(
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

    def classify_feedback(
        self, *, action: str, target_type: str | None = None, payload: dict[str, Any] | None = None
    ) -> str:
        """Clasifica un comando de feedback en el impacto de producto que debe atenderse."""
        return self.feedback_processor.classify_feedback(
            action=action, target_type=target_type, payload=payload
        )

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
        return self.feedback_processor.apply_feedback(
            loop_id,
            action=action,
            feedback=feedback,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            correlation_id=correlation_id,
            expected_version=expected_version,
            now=now,
        )

    def list_feedback(
        self, *, loop_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Lista los feedback commands trazables por loop o proyecto."""
        return self.feedback_processor.list_feedback(loop_id=loop_id, project_id=project_id)

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
        return self.loop_fsm.block(
            loop_id, reason=reason, actor=actor, metadata=metadata, correlation_id=correlation_id, now=now
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
        """Cancela el loop (transición terminal a ``cancelled``) desde cualquier estado no terminal."""
        return self.loop_fsm.cancel(
            loop_id, reason=reason, actor=actor, correlation_id=correlation_id, now=now
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
        """Reanuda un loop bloqueado hacia ``to_state`` (o su estado previo si no se indica)."""
        return self.loop_fsm.unblock(loop_id, to_state=to_state, reason=reason, actor=actor, now=now)

    def record_usage(
        self, loop_id: str, usage_delta: dict[str, Any], *, expected_usage_seq: int | None = None
    ) -> dict[str, Any]:
        """Acumula consumo de presupuesto en ``context['fsm']['usage']['consumed']`` sin tocar la FSM."""
        return self.loop_fsm.record_usage(loop_id, usage_delta, expected_usage_seq=expected_usage_seq)

    def evaluate_stop_conditions(self, loop_id: str, *, now: str | None = None) -> list[dict[str, Any]]:
        """Devuelve las condiciones de parada activas del loop (lectura pura sobre su estado durable)."""
        return self.loop_fsm.evaluate_stop_conditions(loop_id, now=now)

    def enforce_stop_conditions(
        self, loop_id: str, *, now: str | None = None, actor: str = "system"
    ) -> dict[str, Any]:
        """Si alguna condición de parada está activa y el loop sigue activo, lo bloquea con su motivo."""
        return self.loop_fsm.enforce_stop_conditions(loop_id, now=now, actor=actor)

    def resume(self, loop_id: str) -> dict[str, Any]:
        """Reanuda un loop leyendo su estado durable y devuelve qué transiciones admite ahora."""
        return self.loop_fsm.resume(loop_id)
