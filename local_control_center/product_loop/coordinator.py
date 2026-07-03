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
from contextlib import suppress
from pathlib import Path
from typing import Any

from local_control_center.agents.assessment_runner import ProjectAssessmentRunner
from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.developer_agent_contract import DEVELOPER_AGENT_ID
from local_control_center.agents.product_owner_agent import (
    ProductOwnerAgent,
    ProductOwnerAgentRunner,
    ProductOwnerOutputValidationError,
    persist_product_owner_backlog,
)
from local_control_center.agents.product_owner_agent_contract import PRODUCT_OWNER_AGENT_ID
from local_control_center.agents.repository import AgentsRepository
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.backlog.technical_lead_planner import TechnicalLeadPlanner
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.intent_classifier import IntentClassificationInput, IntentClassifier
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import iso_after_seconds, utc_now
from local_control_center.team_scheduler.scheduler import MODES, RISKS, schedule_team
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import SIMILARITY_ACTIONS, ThreadMemoryService
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff
from local_control_center.workspaces_projects.repository import (
    WorkspaceConflictError,
    WorkspaceIsolationError,
    WorkspacesRepository,
)

from .models import FEEDBACK_ACTION_VALUES, FEEDBACK_CLASSIFICATION_VALUES
from .repository import ProductLoopRepository

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
    "branch_ready",
    "executing",
    "qa_running",
    "security_running",
    "review_ready",
    "iteration_planning",
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
    "goal_received": {"workspace_check", "discovery", "discovering", "blocked", "cancelled"},
    "workspace_check": {"runtime_check", "git_check", "blocked", "cancelled"},
    "runtime_check": {"git_check", "discovery", "blocked", "cancelled"},
    "git_check": {"runtime_check", "discovery", "blocked", "cancelled"},
    "discovery": {"awaiting_user", "planning", "brief_ready", "backlog_ready", "blocked", "cancelled"},
    "discovering": {"awaiting_user", "brief_ready", "blocked", "cancelled"},
    "awaiting_user": {"discovering", "brief_ready", "blocked", "cancelled"},
    "planning": {"backlog_ready", "blocked", "cancelled"},
    "brief_ready": {"architecture_review", "backlog_ready", "blocked", "cancelled"},
    "architecture_review": {"backlog_ready", "brief_ready", "blocked", "cancelled"},
    "backlog_ready": {"branch_ready", "iteration_planning", "blocked", "cancelled"},
    "branch_ready": {"executing", "blocked", "cancelled"},
    "iteration_planning": {"executing", "blocked", "cancelled"},
    "executing": {"qa_running", "quality_review", "blocked", "cancelled"},
    "qa_running": {"security_running", "reworking", "blocked", "cancelled"},
    "security_running": {"review_ready", "blocked", "cancelled"},
    "review_ready": {"awaiting_approval", "reworking", "blocked", "cancelled"},
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

_ROLE_TO_SCOPE = {
    "developer": "backend",
    "implementer": "backend",
    "backend_engineer": "backend",
    "frontend_engineer": "frontend",
    "mobile_engineer": "mobile",
    "database_engineer": "database",
    "data_engineer": "data",
    "devops": "infra",
    "devops_engineer": "infra",
    "security_reviewer": "security",
    "security_engineer": "security",
    "pentester": "security",
    "qa": "tests",
    "qa_reviewer": "tests",
    "qa_engineer": "tests",
    "researcher": "research",
    "release_manager": "release",
    "architect": "architecture",
    "software_architect": "architecture",
}
TARGET_REQUIRED_ACTIONS = {"request_changes", "reprioritize", "reject_decision", "reopen_story"}
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


def _changed_files_from_diff(diff: dict[str, Any]) -> list[str]:
    names = [str(item) for item in diff.get("nameOnly") or [] if str(item).strip()]
    if names:
        return names
    changed: list[str] = []
    for item in diff.get("status") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and path not in changed:
            changed.append(path)
    return changed


def _runtime_changed_files(runtime_result: dict[str, Any]) -> list[str]:
    summary = runtime_result.get("diffSummary") if isinstance(runtime_result.get("diffSummary"), dict) else {}
    return [str(item) for item in summary.get("changedFiles") or [] if str(item).strip()]


def _review_from_diff(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": diff.get("state"),
        "changedFiles": _changed_files_from_diff(diff),
        "branch": diff.get("branch"),
        "headCommit": diff.get("headCommit"),
        "diffStat": diff.get("diffStat") or "",
        "patch": diff.get("patch") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
        "toolCalls": diff.get("toolCalls") or [],
        "policyDecisionIds": diff.get("policyDecisionIds") or [],
    }


def _review_from_runtime(runtime_result: dict[str, Any]) -> dict[str, Any]:
    summary = runtime_result.get("diffSummary") if isinstance(runtime_result.get("diffSummary"), dict) else {}
    return {
        "state": summary.get("state") or "runtime_reported",
        "changedFiles": _runtime_changed_files(runtime_result),
        "branch": summary.get("branch"),
        "headCommit": summary.get("headCommit"),
        "diffStat": summary.get("diffStat") or "",
        "patch": summary.get("patch") or "",
        "patchSizeBytes": summary.get("patchSizeBytes") or 0,
        "truncated": bool(summary.get("truncated", False)),
        "toolCalls": [],
        "policyDecisionIds": [],
    }


def _diff_ref_from_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "git_diff",
        "state": review.get("state"),
        "files": review.get("changedFiles") or [],
        "branch": review.get("branch"),
        "headCommit": review.get("headCommit"),
        "diffStat": review.get("diffStat") or "",
        "patchSizeBytes": review.get("patchSizeBytes") or 0,
        "truncated": bool(review.get("truncated", False)),
    }


def _diff_summary_from_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": review.get("state"),
        "changedFiles": review.get("changedFiles") or [],
        "diffStat": review.get("diffStat") or "",
        "patchSizeBytes": review.get("patchSizeBytes") or 0,
        "truncated": bool(review.get("truncated", False)),
    }


def _thread_agent_role_for_state(state: str) -> str:
    if state == "executing":
        return "developer"
    if state == "qa_running":
        return "qa_reviewer"
    if state == "security_running":
        return "security_reviewer"
    return "aido_lead"


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

    def __init__(self, connection: sqlite3.Connection, *, root: str | Path | None = None):
        self.connection = connection
        self.repository = ProductLoopRepository(connection)
        self.backlog = BacklogRepository(connection)
        self.discovery = ProductDiscoveryRepository(connection)
        self.root = Path(root).resolve(strict=False) if root is not None else None
        self.agents = AgentsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.events = EventBus(connection)
        self.jobs = JobsRepository(connection)

    @staticmethod
    def _state_deadline(state: str, timeouts: dict[str, Any] | None, now: str) -> str | None:
        seconds = (timeouts or {}).get(state)
        if seconds is None:
            return None
        return iso_after_seconds(now, float(seconds))

    def _durable_run_context(self, loop: dict[str, Any]) -> dict[str, Any]:
        return dict((loop.get("context") or {}).get("durableRun") or {})

    def _durable_run_patch(
        self,
        loop: dict[str, Any],
        updates: dict[str, Any],
        *,
        evidence_package_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        durable = {**self._durable_run_context(loop), **redact_secrets(updates)}
        ids = [str(item) for item in durable.get("evidencePackageIds", []) if str(item).strip()]
        for evidence_id in evidence_package_ids or []:
            if evidence_id and evidence_id not in ids:
                ids.append(evidence_id)
        durable["evidencePackageIds"] = ids
        durable["updatedAt"] = utc_now()
        return {"durableRun": durable}

    def _record_loop_event(
        self,
        *,
        project_id: str,
        event_type: str,
        loop_id: str,
        payload: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> None:
        self.events.record_event(
            project_id=project_id,
            event_type=event_type,
            payload={"loopId": loop_id, **redact_secrets(payload or {})},
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
        ThreadsRepository(self.connection).record_event(
            thread_id=thread_id,
            type=event_type,
            agent_role=agent_role,
            payload=payload or {},
        )

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
    ) -> dict[str, Any]:
        updated = self.transition(
            loop["id"],
            to_state=to_state,
            reason=reason,
            actor=actor,
            trigger=trigger,
            context_patch=context_patch,
            metadata=metadata,
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
        verdict = (
            "passed"
            if status
            in {
                "completed",
                "awaiting_approval",
                "reworking",
                "awaiting_user",
                "needs_input",
                "brief_ready",
                "backlog_ready",
            }
            else "blocked"
        )
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

    def _block_run(
        self,
        loop: dict[str, Any],
        *,
        stage: str,
        reason: str,
        actor: str,
        details: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
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
        BlockerRemediationService(self.connection, root=self.root).create_for_blocked_run(
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
        actor: str = "operator",
    ) -> dict[str, Any]:
        threads = ThreadsRepository(self.connection)
        clean_title = title or message.strip().splitlines()[0][:80] or "Product Loop"
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
                    metadata={"source": "product_loop"},
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
            metadata={"source": "product_loop", "legacySessionId": session_id},
        )
        return {
            "projectThreadId": thread["id"],
            "messageId": created_message["id"],
            "title": thread["title"],
        }

    @staticmethod
    def _memory_decision_resolved(request_meta: dict[str, Any]) -> bool:
        mode = str(
            request_meta.get("mode")
            or request_meta.get("functionalityDecision")
            or request_meta.get("memoryMode")
            or ""
        ).strip()
        return mode in SIMILARITY_ACTIONS

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
        threads.record_event(
            thread_id=thread_id,
            type="functionality_detected",
            agent_role="aido_lead",
            payload={**metadata, "decisionId": decision["id"]},
        )
        threads.set_status(thread_id, "waiting_decision")
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

    def _attach_thread_artifacts(
        self, *, thread_id: str | None, artifacts: list[dict[str, Any]]
    ) -> None:
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

    def _product_owner_flow_status(
        self, result: dict[str, Any], output: dict[str, Any] | None = None
    ) -> str:
        output = output if output is not None else self._product_owner_output(result)
        status = str(result.get("status") or "").strip().lower()
        output_status = str(output.get("status") or "").strip().lower()
        questions = output.get("questions") or result.get("questions") or []
        decisions = output.get("decisions") or result.get("blockingDecisions") or result.get("decisions") or []
        if status in {"runtime_unavailable", "failed_validation", "failed"}:
            return "blocked"
        if status in {"needs_input", "questions_required"} or output_status in {
            "needs_input",
            "questions_required",
        }:
            return "needs_input"
        if status == "blocked" and (questions or decisions):
            return "needs_input"
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
    ) -> dict[str, Any]:
        initiative = result.get("initiative")
        if isinstance(initiative, dict) and initiative.get("id"):
            try:
                return self.discovery.get_initiative(str(initiative["id"]))
            except KeyError:
                pass
        initiative_id = result.get("initiativeId") or output.get("initiativeId")
        if initiative_id:
            try:
                return self.discovery.get_initiative(str(initiative_id))
            except KeyError:
                pass
        return self.discovery.create_initiative(
            {
                "projectId": project_id,
                "title": title,
                "summary": output.get("summary") or message,
                "status": "discovery",
                "priority": "medium",
                "owner": PRODUCT_OWNER_AGENT_ID,
                "metadata": {"source": "product_loop_coordinator"},
            }
        )

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

    def _persist_clarification_questions(
        self,
        *,
        project_id: str,
        initiative_id: str,
        output: dict[str, Any],
        thread_id: str | None,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        threads = ThreadsRepository(self.connection) if thread_id else None
        for item in output.get("questions") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").startswith("clarification-question-"):
                try:
                    record = self.discovery.get_clarification_question(str(item["id"]))
                    records.append(record)
                    continue
                except KeyError:
                    pass
            question_text = str(item.get("question") or item.get("prompt") or "").strip()
            if not question_text:
                continue
            record = self.discovery.create_clarification_question(
                {
                    "projectId": project_id,
                    "initiativeId": initiative_id,
                    "question": question_text,
                    "priority": item.get("priority") or "high" if item.get("blocking", True) else "medium",
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
                threads.create_decision(
                    thread_id=thread_id,
                    title=question_text[:120],
                    prompt=question_text,
                    options=self._question_options(item),
                    metadata={
                        "source": PRODUCT_OWNER_AGENT_ID,
                        "clarificationQuestionId": record["id"],
                    },
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
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        threads = ThreadsRepository(self.connection) if thread_id else None
        for item in output.get("decisions") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").startswith("product-decision-"):
                try:
                    records.append(self.discovery.get_product_decision(str(item["id"])))
                    continue
                except KeyError:
                    pass
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
                    },
                }
            )
            records.append(record)
            if threads and thread_id and status not in {"accepted", "resolved"}:
                threads.create_decision(
                    thread_id=thread_id,
                    title=title[:120],
                    prompt=str(item.get("question") or title),
                    options=self._question_options(item),
                    metadata={"source": PRODUCT_OWNER_AGENT_ID, "productDecisionId": record["id"]},
                )
        return records

    def _persist_product_owner_backlog(
        self,
        *,
        project_id: str,
        output: dict[str, Any],
        result: dict[str, Any],
        product_owner_output_id: str | None,
    ) -> list[dict[str, Any]]:
        persisted = result.get("epics")
        if isinstance(persisted, list) and persisted and isinstance(persisted[0], dict) and "epic" in persisted[0]:
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
    ) -> list[dict[str, Any]]:
        stories = self._stories_from_backlog(backlog)
        existing = [
            task
            for story in stories
            for task in self.backlog.list_agent_tasks(story_id=story["id"])
        ]
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
            "risk": (team_schedule or {}).get("risk") or ((team_schedule or {}).get("intent") or {}).get("risk"),
            "availableTeam": (team_schedule or {}).get("roles") or [],
            "teamSchedule": team_schedule or {},
        }
        if technical_lead_runner is not None and hasattr(technical_lead_runner, "generate_agent_tasks"):
            planned = technical_lead_runner.generate_agent_tasks(payload)
        else:
            planned = TechnicalLeadPlanner().plan(payload)
        if isinstance(planned, dict):
            specs = planned.get("agent_tasks") or planned.get("agentTasks") or []
            dependency_specs = planned.get("task_dependencies") or planned.get("taskDependencies") or []
        else:
            specs = planned
            dependency_specs = []
        tasks: list[dict[str, Any]] = []
        planned_to_persisted: dict[str, str] = {}
        for spec in specs or []:
            if not isinstance(spec, dict):
                continue
            story_id = str(spec.get("storyId") or "").strip()
            if not story_id:
                continue
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
                    "role": spec.get("role") or "developer",
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

    def _team_mode(self, request_meta: dict[str, Any]) -> str:
        mode = str(
            request_meta.get("teamMode") or request_meta.get("team_mode") or request_meta.get("mode") or "balanced"
        ).strip().lower()
        return mode if mode in MODES else "balanced"

    def _team_risk(
        self,
        *,
        request_meta: dict[str, Any],
        intent: dict[str, Any],
        output: dict[str, Any],
        message: str,
    ) -> str:
        risk = str(request_meta.get("risk") or intent.get("risk") or "").strip().lower()
        if risk in RISKS:
            return risk
        text = " ".join(
            [
                message,
                str(output.get("summary") or ""),
                " ".join(str(item) for item in output.get("risks") or []),
            ]
        ).lower()
        if any(marker in text for marker in ("critical", "prod", "production", "payment", "pii")):
            return "high"
        return "medium"

    def _team_scope(
        self,
        *,
        message: str,
        intent: dict[str, Any],
        output: dict[str, Any],
        agent_tasks: list[dict[str, Any]],
    ) -> list[str]:
        scope: dict[str, None] = {}
        for intent_name in intent.get("intents") or []:
            value = str(intent_name).strip().lower()
            if value:
                scope.setdefault(value, None)
        for role in intent.get("requiredRoles") or []:
            mapped = _ROLE_TO_SCOPE.get(str(role).strip().lower())
            if mapped:
                scope.setdefault(mapped, None)
        text = " ".join(
            [
                message,
                str(output.get("summary") or ""),
                str((output.get("productBriefPatch") or {}).get("scope") or ""),
                " ".join(str(item) for item in output.get("risks") or []),
            ]
        ).lower()
        keyword_scope = {
            "backend": ("backend", "api", "server", "fastapi", "service"),
            "frontend": ("frontend", "ui", "web", "react", "screen"),
            "mobile": ("mobile", "ios", "android", "react native"),
            "database": ("database", "schema", "migration", "sqlite", "sql"),
            "data": ("data", "analytics", "pipeline", "ml"),
            "infra": ("infra", "devops", "deploy", "ci", "kubernetes", "docker"),
            "security": ("security", "auth", "login", "token", "secret", "pentest", "exploit"),
            "research": ("research", "investigate", "source", "benchmark"),
            "release": ("release", "publish", "rollback"),
            "refactor": ("refactor", "restructure", "decouple"),
        }
        for scope_name, markers in keyword_scope.items():
            if any(marker in text for marker in markers):
                scope.setdefault(scope_name, None)
        for task in agent_tasks:
            role = str(task.get("role") or "").strip().lower()
            mapped = _ROLE_TO_SCOPE.get(role)
            if mapped:
                scope.setdefault(mapped, None)
        if not scope:
            scope["backend"] = None
        return list(scope)

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
        plan = schedule_team(scope=scope, risk=risk, mode=mode)
        return {**plan, "intent": intent}

    def _profile_by_role(self) -> dict[str, dict[str, Any]]:
        profiles = self.agents.list_agent_profiles()
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
        profiles_by_role = self._profile_by_role()
        assignments: list[dict[str, Any]] = []
        for role_plan in team_schedule["roles"]:
            role = str(role_plan["role"])
            profile = profiles_by_role.get(role)
            if profile is None:
                continue
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
                            "qualityGates": role_plan["qualityGates"],
                            "reviewerPolicy": role_plan["reviewerPolicy"],
                        },
                    }
                )
            )
        return assignments

    def _requires_brief_approval(
        self, *, request_meta: dict[str, Any], result: dict[str, Any], output: dict[str, Any]
    ) -> bool:
        autonomy = request_meta.get("autonomy")
        if autonomy is None:
            autonomy = (result.get("autonomy") or output.get("autonomy") or {})
        if isinstance(autonomy, str):
            return autonomy.strip().lower() in {"guided", "recommended"}
        if isinstance(autonomy, dict):
            level = str(autonomy.get("level") or autonomy.get("mode") or "").strip().lower()
            return level in {"guided", "recommended"}
        return False

    @staticmethod
    def _research_policy(request_meta: dict[str, Any]) -> dict[str, Any]:
        policy = request_meta.get("researchPolicy") if isinstance(request_meta.get("researchPolicy"), dict) else {}
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
        impact = str(
            decision.get("impact")
            or decision.get("risk")
            or decision.get("severity")
            or decision.get("priority")
            or ""
        ).strip().lower()
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
            str(decision.get("title") or decision.get("decision") or decision.get("recommendation") or "").strip()
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
            "taskId": f"product-loop-research-{loop_id.replace('product-loop-', '')[:12]}",
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
    ) -> dict[str, Any]:
        """Run one user message through the durable Product Loop control plane.

        This is intentionally synchronous and fail-closed: every message creates or reuses a persisted
        project thread/message
        and then ends in a real question/brief/backlog/execution/rework/block/approval result. Agent,
        runtime and git dependencies can be injected by tests; production defaults use the real runners.
        """
        message_text = str(message or "").strip()
        if not message_text:
            raise ProductLoopTransitionError("Product Loop user message is required.")
        effective_root = Path(root).resolve(strict=False) if root is not None else self.root
        resolved_title = title or message_text.splitlines()[0][:80] or "Product Loop"
        request_meta = redact_secrets(run_metadata or {})
        thread = self._create_thread(
            project_id=project_id,
            message=message_text,
            title=resolved_title,
            session_id=session_id,
            thread_id=thread_id,
            message_id=request_meta.get("messageId") if isinstance(request_meta.get("messageId"), str) else None,
            actor=actor,
        )
        thread_id = thread["projectThreadId"]
        loop = self.start(
            project_id=project_id,
            title=resolved_title,
            context={
                "durableRun": {
                    "status": INITIAL_STATE,
                    "thread": thread,
                    "message": message_text,
                    "requestMeta": request_meta,
                    "evidencePackageIds": [],
                }
            },
            correlation_id=thread_id,
            actor=actor,
            reason="Product Loop started from a user message.",
        )
        self._record_loop_event(
            project_id=project_id,
            event_type="product_loop.message_received",
            loop_id=loop["id"],
            payload={"thread": thread},
            thread_id=thread_id,
        )
        existing_functionality = (
            None
            if self._memory_decision_resolved(request_meta)
            else self._existing_functionality_match(project_id=project_id, message=message_text)
        )
        if existing_functionality is not None:
            return self._block_existing_functionality(
                loop=loop,
                thread_id=thread_id,
                functionality=existing_functionality,
                actor=actor,
            )

        assignments = self._ensure_delivery_agents(project_id)
        loop = self._transition_run_state(
            loop,
            to_state="workspace_check",
            reason="User message thread and delivery agents are registered.",
            trigger="workspace_check",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {
                    "status": "workspace_check",
                    "thread": thread,
                    "requestMeta": request_meta,
                    "agentAssignments": assignments,
                },
            ),
            thread_id=thread_id,
        )
        if effective_root is None:
            return self._block_run(
                loop,
                stage="workspace_check",
                reason="ProductLoopCoordinator root is required to create an isolated workspace.",
                actor=actor,
                thread_id=thread_id,
            )

        git = git_service or GitWorkspaceService(self.connection, root=effective_root)
        product_owner = product_owner_runner or ProductOwnerAgentRunner(self.connection, root=effective_root)

        loop = self._transition_run_state(
            loop,
            to_state="git_check",
            reason="Checking git workspace cleanliness before worktree allocation.",
            trigger="git_check",
            actor=actor,
            context_patch=self._durable_run_patch(loop, {"status": "git_check"}),
            thread_id=thread_id,
        )
        git_state = git.status(project_id)
        if git_state.get("status") != "completed":
            reason = str(git_state.get("reason") or "Git status did not complete.")
            return self._block_run(
                loop, stage="git", reason=reason, actor=actor, details=git_state, thread_id=thread_id
            )
        if bool(git_state.get("dirty")):
            reason = "Project git tree is dirty; Product Loop execution requires a clean base."
            return self._block_run(
                loop, stage="git", reason=reason, actor=actor, details=git_state, thread_id=thread_id
            )

        loop = self._transition_run_state(
            loop,
            to_state="runtime_check",
            reason="Checking executable ProductOwnerAgent runtime after git_check.",
            trigger="product_owner_runtime_check",
            actor=actor,
            context_patch=self._durable_run_patch(loop, {"status": "runtime_check"}),
            thread_id=thread_id,
        )
        if hasattr(product_owner, "status"):
            product_owner_readiness = product_owner.status(preferred_runtime=preferred_runtime)
        else:
            product_owner_readiness = {
                "executable": True,
                "selectedRuntimeId": preferred_runtime or "injected_product_owner_runner",
                "reason": "Injected ProductOwnerAgent runner has no readiness hook.",
            }
        self._record_thread_event(
            thread_id=thread_id,
            event_type="runtime_selected",
            agent_role="product_owner",
            payload={
                "loopId": loop["id"],
                "runtimeId": product_owner_readiness.get("selectedRuntimeId") or preferred_runtime,
                "executable": bool(product_owner_readiness.get("executable")),
                "reason": product_owner_readiness.get("reason"),
            },
        )
        if not bool(product_owner_readiness.get("executable")):
            reason = str(
                product_owner_readiness.get("reason")
                or "No executable ProductOwnerAgent runtime is configured."
            )
            return self._block_run(
                loop,
                stage="product_owner_runtime",
                reason=reason,
                actor=actor,
                details=product_owner_readiness,
                thread_id=thread_id,
            )

        assessment_result: dict[str, Any] | None = None
        if self._project_is_existing(project_id):
            assessment = assessment_runner or ProjectAssessmentRunner(self.connection, root=effective_root)
            try:
                assessment_result = assessment.run(project_id)
            except Exception as error:
                return self._block_run(
                    loop,
                    stage="project_assessment",
                    reason=str(redact_secrets(str(error))),
                    actor=actor,
                    details={"projectId": project_id},
                    thread_id=thread_id,
                )
            if assessment_result.get("status") != "completed":
                reason = str(assessment_result.get("reason") or "Project assessment did not complete.")
                return self._block_run(
                    loop,
                    stage="project_assessment",
                    reason=reason,
                    actor=actor,
                    details=assessment_result,
                    thread_id=thread_id,
                )

        loop = self._transition_run_state(
            loop,
            to_state="discovery",
            reason="ProductOwnerAgent discovery started after git and assessment gates.",
            trigger="product_owner_discovery",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {
                    "status": "discovery",
                    "assessment": redact_secrets(assessment_result or {}),
                    "productOwner": {"status": "running"},
                },
            ),
            thread_id=thread_id,
        )

        product_owner_task_id = f"product-owner-{loop['id'].replace('product-loop-', '')[:12]}"
        try:
            product_owner_workspace = WorkspacesRepository(
                self.connection, root=effective_root
            ).allocate_workspace(
                project_id=project_id,
                task_id=product_owner_task_id,
                agent_id=PRODUCT_OWNER_AGENT_ID,
                reason="ProductLoopCoordinator ProductOwnerAgent workspace",
                branch_name=f"codex/product-owner-{loop['id'][-12:]}",
            )
        except (WorkspaceConflictError, WorkspaceIsolationError, ValueError, KeyError) as error:
            return self._block_run(
                loop,
                stage="product_owner_workspace",
                reason=str(error),
                actor=actor,
                details={"taskId": product_owner_task_id},
                thread_id=thread_id,
            )

        product_owner_payload = {
            "projectId": project_id,
            "workspaceId": product_owner_workspace["id"],
            "taskId": product_owner_task_id,
            "idea": message_text,
            "preferredRuntime": preferred_runtime,
            "assessment": redact_secrets(assessment_result or {}),
            "metadata": {
                "loopId": loop["id"],
                "thread": thread,
                "source": "product_loop_coordinator",
            },
        }
        try:
            product_owner_result = product_owner.run(product_owner_payload)
        except Exception as error:
            return self._block_run(
                loop,
                stage="product_owner",
                reason=str(redact_secrets(str(error))),
                actor=actor,
                details={"workspaceId": product_owner_workspace["id"]},
                thread_id=thread_id,
            )

        try:
            output = self._product_owner_output(product_owner_result)
            product_owner_status = self._product_owner_flow_status(product_owner_result, output=output)
        except ProductOwnerOutputValidationError as error:
            raw_output = product_owner_result.get("output") if isinstance(product_owner_result, dict) else None
            details = {
                "status": product_owner_result.get("status") if isinstance(product_owner_result, dict) else None,
                "reason": product_owner_result.get("reason") if isinstance(product_owner_result, dict) else None,
                "outputType": type(raw_output).__name__,
            }
            return self._block_run(
                loop,
                stage="product_owner",
                reason=str(redact_secrets(str(error))),
                actor=actor,
                details=details,
                thread_id=thread_id,
            )
        product_owner_artifact = self._write_json_artifact(
            root=effective_root,
            project_id=project_id,
            name="product_owner_output.json",
            kind="product_owner_output",
            payload={"status": product_owner_status, "result": product_owner_result, "output": output},
        )
        initiative = self._ensure_product_initiative(
            project_id=project_id,
            message=message_text,
            title=resolved_title,
            result=product_owner_result,
            output=output,
        )
        brief = self._persist_product_brief(
            project_id=project_id,
            initiative_id=initiative["id"],
            title=resolved_title,
            result=product_owner_result,
            output=output,
        )
        brief_artifact = self._write_json_artifact(
            root=effective_root,
            project_id=project_id,
            name="product_brief.json",
            kind="product_brief",
            payload={"brief": brief, "productBriefPatch": output.get("productBriefPatch") or {}},
        )
        product_owner_output_record = self._persist_product_owner_output_record(
            project_id=project_id,
            initiative_id=initiative["id"],
            brief_id=brief["id"],
            output=output,
            result=product_owner_result,
            artifact_id=product_owner_artifact["id"],
        )
        clarification_questions = self._persist_clarification_questions(
            project_id=project_id,
            initiative_id=initiative["id"],
            output=output,
            thread_id=thread_id,
        )
        product_decisions = self._persist_product_decisions(
            project_id=project_id,
            initiative_id=initiative["id"],
            brief_id=brief["id"],
            output=output,
            thread_id=thread_id,
        )
        po_artifacts = [product_owner_artifact, brief_artifact]
        self._attach_thread_artifacts(thread_id=thread_id, artifacts=po_artifacts)
        po_artifact_ids = [artifact["id"] for artifact in po_artifacts]
        po_evidence = self._record_run_evidence(
            project_id=project_id,
            loop_id=loop["id"],
            stage="product_owner",
            status=product_owner_status,
            reason=str(product_owner_result.get("reason") or output.get("recommendedNextAction") or ""),
            details={
                "status": product_owner_status,
                "productOwnerOutputId": product_owner_output_record["id"],
                "clarificationQuestionIds": [item["id"] for item in clarification_questions],
                "productDecisionIds": [item["id"] for item in product_decisions],
            },
            workspace_id=product_owner_workspace["id"],
            artifact_ids=po_artifact_ids,
        )
        for artifact_id in po_artifact_ids:
            self.evidence.attach_artifact_to_evidence(
                artifact_id=artifact_id, evidence_package_id=po_evidence["id"]
            )
        product_owner_context = {
            "status": product_owner_status,
            "reason": product_owner_result.get("reason") or output.get("recommendedNextAction") or "",
            "workspaceId": product_owner_workspace["id"],
            "productOwnerOutputId": product_owner_output_record["id"],
            "briefId": brief["id"],
            "artifactIds": po_artifact_ids,
            "evidencePackageId": po_evidence["id"],
            "clarificationQuestionIds": [item["id"] for item in clarification_questions],
            "productDecisionIds": [item["id"] for item in product_decisions],
        }
        evidence_ids = [*self._external_evidence_ids(product_owner_result), po_evidence["id"]]
        self._record_thread_event(
            thread_id=thread_id,
            event_type="product_owner_completed",
            agent_role="product_owner",
            payload={"loopId": loop["id"], **product_owner_context},
        )

        if product_owner_status == "needs_input":
            if thread_id:
                with suppress(KeyError, ValueError):
                    ThreadsRepository(self.connection).set_status(thread_id, "waiting_decision")
            awaiting = self._transition_run_state(
                loop,
                to_state="awaiting_user",
                reason=product_owner_context["reason"]
                or "ProductOwnerAgent requires product clarification before development.",
                trigger="product_owner_needs_input",
                actor=actor,
                context_patch=self._durable_run_patch(
                    loop,
                    {
                        "status": "awaiting_user",
                        "productOwner": product_owner_context,
                    },
                    evidence_package_ids=evidence_ids,
                ),
                thread_id=thread_id,
            )
            return self._run_result(
                awaiting,
                status="awaiting_user",
                reason=product_owner_context["reason"]
                or "ProductOwnerAgent requires product clarification before development.",
                evidence_package=po_evidence,
            )

        if product_owner_status == "blocked":
            return self._block_run(
                loop,
                stage="product_owner",
                reason=str(product_owner_result.get("reason") or "ProductOwnerAgent did not produce a usable output."),
                actor=actor,
                details=product_owner_result,
                thread_id=thread_id,
            )

        research_required_decisions = self._high_impact_technical_decisions_requiring_research(
            request_meta=request_meta,
            output=output,
        )
        if research_required_decisions:
            research_job = self._queue_required_research(
                project_id=project_id,
                thread_id=thread_id,
                message_id=thread["messageId"],
                workspace_id=product_owner_workspace["id"],
                loop_id=loop["id"],
                message=message_text,
                request_meta=request_meta,
                decisions=research_required_decisions,
            )
            return self._block_run(
                loop,
                stage="research",
                reason=(
                    "ResearchAgent evidence is required before accepting high-impact technical "
                    "decisions."
                ),
                actor=actor,
                details={
                    "jobId": research_job["id"],
                    "researchStatus": "research_required",
                    "decisions": research_required_decisions,
                    "researchPolicy": self._research_policy(request_meta),
                },
                thread_id=thread_id,
            )

        if product_owner_status == "brief_ready":
            approval = None
            if self._requires_brief_approval(
                request_meta=request_meta, result=product_owner_result, output=output
            ):
                approval = self._create_brief_approval(
                    project_id=project_id,
                    loop_id=loop["id"],
                    brief=brief,
                    artifact_ids=po_artifact_ids,
                )
                product_owner_context["briefApproval"] = approval
            brief_ready = self._transition_run_state(
                loop,
                to_state="brief_ready",
                reason=product_owner_context["reason"] or "ProductOwnerAgent produced a product brief.",
                trigger="product_owner_brief_ready",
                actor=actor,
                context_patch=self._durable_run_patch(
                    loop,
                    {
                        "status": "brief_ready",
                        "productOwner": product_owner_context,
                        "briefApproval": approval,
                    },
                    evidence_package_ids=evidence_ids,
                ),
                thread_id=thread_id,
            )
            return self._run_result(
                brief_ready,
                status="brief_ready",
                reason=product_owner_context["reason"] or "ProductOwnerAgent produced a product brief.",
                evidence_package=po_evidence,
            )

        backlog = self._persist_product_owner_backlog(
            project_id=project_id,
            output=output,
            result=product_owner_result,
            product_owner_output_id=product_owner_output_record["id"],
        )
        if not backlog:
            return self._block_run(
                loop,
                stage="backlog",
                reason="ProductOwnerAgent returned backlog_ready without epics/user_stories/acceptance_criteria.",
                actor=actor,
                details={"productOwnerOutputId": product_owner_output_record["id"]},
                thread_id=thread_id,
            )
        backlog_artifact = self._write_json_artifact(
            root=effective_root,
            project_id=project_id,
            name="backlog.json",
            kind="product_backlog",
            payload={"backlog": backlog, "productOwnerOutputId": product_owner_output_record["id"]},
        )
        self._attach_thread_artifacts(thread_id=thread_id, artifacts=[backlog_artifact])
        self.evidence.attach_artifact_to_evidence(
            artifact_id=backlog_artifact["id"], evidence_package_id=po_evidence["id"]
        )
        product_owner_context["artifactIds"] = [*po_artifact_ids, backlog_artifact["id"]]
        preliminary_team_schedule = self._team_schedule(
            message=message_text,
            request_meta=request_meta,
            output=output,
            assessment_result=assessment_result,
            git_state=git_state,
        )
        agent_tasks = self._generate_agent_tasks(
            project_id=project_id,
            loop_id=loop["id"],
            backlog=backlog,
            product_owner_output_id=product_owner_output_record["id"],
            technical_lead_runner=technical_lead_runner,
            team_schedule=preliminary_team_schedule,
            product_owner_output=output,
            assessment_result=assessment_result,
            git_state=git_state,
        )
        if not agent_tasks:
            return self._block_run(
                loop,
                stage="technical_lead",
                reason="TechnicalLead did not generate agent_tasks; DeveloperAgent execution is not allowed.",
                actor=actor,
                details={"productOwnerOutputId": product_owner_output_record["id"]},
                thread_id=thread_id,
            )
        team_schedule = self._team_schedule(
            message=message_text,
            request_meta=request_meta,
            output=output,
            assessment_result=assessment_result,
            git_state=git_state,
            agent_tasks=agent_tasks,
        )
        team_assignments = self._create_team_assignments(
            project_id=project_id,
            loop_id=loop["id"],
            agent_tasks=agent_tasks,
            team_schedule=team_schedule,
        )

        loop = self._transition_run_state(
            loop,
            to_state="planning",
            reason="TechnicalLead generated agent_tasks from the ProductOwnerAgent backlog.",
            trigger="technical_lead_planning",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {
                    "status": "planning",
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                    "agentAssignments": team_assignments,
                },
                evidence_package_ids=evidence_ids,
            ),
            thread_id=thread_id,
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type="agent_tasks_ready",
            agent_role="technical_lead",
            payload={
                "loopId": loop["id"],
                "agentTaskIds": [task["id"] for task in agent_tasks],
                "productOwnerOutputId": product_owner_output_record["id"],
                "teamSchedule": team_schedule["summary"],
                "agentAssignmentIds": [assignment["id"] for assignment in team_assignments],
            },
        )
        loop = self._transition_run_state(
            loop,
            to_state="backlog_ready",
            reason="ProductOwnerAgent backlog and TechnicalLead agent_tasks are persisted.",
            trigger="product_owner_backlog_ready",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {
                    "status": "backlog_ready",
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                    "agentAssignments": team_assignments,
                },
                evidence_package_ids=evidence_ids,
            ),
            thread_id=thread_id,
        )

        runtime = runtime_runner or DeveloperAgentRunner(self.connection, root=effective_root)
        readiness = runtime.status(preferred_runtime=preferred_runtime)
        self._record_thread_event(
            thread_id=thread_id,
            event_type="runtime_selected",
            agent_role="developer",
            payload={
                "loopId": loop["id"],
                "runtimeId": readiness.get("selectedRuntimeId") or preferred_runtime,
                "executable": bool(readiness.get("executable")),
                "reason": readiness.get("reason"),
            },
        )
        if not bool(readiness.get("executable")):
            reason = str(readiness.get("reason") or "No executable DeveloperAgent runtime is configured.")
            return self._block_run(
                loop, stage="runtime", reason=reason, actor=actor, details=readiness, thread_id=thread_id
            )

        task_id = f"product-loop-{loop['id'].replace('product-loop-', '')[:12]}"
        try:
            workspace = WorkspacesRepository(self.connection, root=effective_root).allocate_workspace(
                project_id=project_id,
                task_id=task_id,
                agent_id=DEVELOPER_AGENT_ID,
                reason="ProductLoopCoordinator durable execution workspace",
                branch_name=f"codex/product-loop-{loop['id'][-12:]}",
            )
        except (WorkspaceConflictError, WorkspaceIsolationError, ValueError, KeyError) as error:
            return self._block_run(
                loop,
                stage="workspace",
                reason=str(error),
                actor=actor,
                details={"taskId": task_id},
                thread_id=thread_id,
            )

        loop = self._transition_run_state(
            loop,
            to_state="branch_ready",
            reason="Isolated workspace/worktree is ready for runtime execution.",
            trigger="branch_ready",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {
                    "status": "branch_ready",
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "workspaceIsolationType": workspace["isolationType"],
                },
            ),
            thread_id=thread_id,
        )
        loop = self._transition_run_state(
            loop,
            to_state="executing",
            reason="Executing DeveloperAgent runtime in the isolated workspace.",
            trigger="runtime_execution",
            actor=actor,
            context_patch=self._durable_run_patch(loop, {"status": "executing"}),
            thread_id=thread_id,
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type="agent_running",
            agent_role="developer",
            payload={
                "loopId": loop["id"],
                "agentId": DEVELOPER_AGENT_ID,
                "role": "developer",
                "runtimeId": readiness.get("selectedRuntimeId") or preferred_runtime,
                "workspaceId": workspace["id"],
                "assignmentId": task_id,
            },
        )
        try:
            runtime_result = runtime.run(
                {
                    "projectId": project_id,
                    "workspaceId": workspace["id"],
                    "taskId": task_id,
                    "instruction": message_text,
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                    "agentAssignments": team_assignments,
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "backlogArtifactId": backlog_artifact["id"],
                    "preferredRuntime": preferred_runtime,
                    "qaCommands": qa_commands or [],
                    "requireApproval": True,
                    "metadata": {"loopId": loop["id"], "thread": thread},
                }
            )
        except Exception as error:
            return self._block_run(
                loop,
                stage="runtime",
                reason=str(redact_secrets(str(error))),
                actor=actor,
                details={"workspaceId": workspace["id"]},
                thread_id=thread_id,
            )

        runtime_status = str(runtime_result.get("status") or "failed")
        evidence_ids = self._external_evidence_ids(runtime_result)
        review = _review_from_runtime(runtime_result)
        if workspace["isolationType"] == "git_worktree":
            diff = capture_git_diff(
                Path(workspace["path"]),
                connection=self.connection,
                root=effective_root,
                project_id=project_id,
                workspace_id=workspace["id"],
                task_id=f"{task_id}.review_diff",
            )
            review = _review_from_diff(diff)
            if review["state"] != "captured":
                return self._block_run(
                    loop,
                    stage="review",
                    reason="Product Loop review diff could not be captured from the assigned git worktree.",
                    actor=actor,
                    details={"workspaceId": workspace["id"], "review": review},
                    thread_id=thread_id,
                )
            if not review["changedFiles"]:
                return self._block_run(
                    loop,
                    stage="review",
                    reason="Product Loop runtime completed without real changed files in the assigned worktree.",
                    actor=actor,
                    details={"workspaceId": workspace["id"], "review": review},
                    thread_id=thread_id,
                )
        loop = self._transition_run_state(
            loop,
            to_state="qa_running",
            reason="Runtime finished; QA evidence is being evaluated.",
            trigger="qa_running",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {"status": "qa_running", "runtimeResult": runtime_result, "review": review},
                evidence_package_ids=evidence_ids,
            ),
            thread_id=thread_id,
        )
        qa_verdict = str((runtime_result.get("evidencePackage") or {}).get("qaVerdict") or "")
        if runtime_status == "qa_failed" or qa_verdict == "failed":
            reason = str(runtime_result.get("reason") or "QA failed and requires rework.")
            evidence = self._record_run_evidence(
                project_id=project_id,
                loop_id=loop["id"],
                stage="qa",
                status="reworking",
                reason=reason,
                details=runtime_result,
            )
            reworked = self._transition_run_state(
                loop,
                to_state=REWORK_STATE,
                reason=reason,
                trigger="qa_failed",
                actor=actor,
                context_patch=self._durable_run_patch(
                    loop,
                    {
                        "status": REWORK_STATE,
                        "rework": {"source": "qa", "reason": reason, "runtimeStatus": runtime_status},
                    },
                    evidence_package_ids=[*evidence_ids, evidence["id"]],
                ),
                metadata={"evidencePackageId": evidence["id"]},
                thread_id=thread_id,
            )
            return self._run_result(reworked, status=REWORK_STATE, reason=reason, evidence_package=evidence)
        if runtime_status not in {"completed", "evidence_ready"}:
            reason = str(runtime_result.get("reason") or f"Runtime ended with status {runtime_status}.")
            return self._block_run(
                loop, stage="runtime", reason=reason, actor=actor, details=runtime_result, thread_id=thread_id
            )

        loop = self._transition_run_state(
            loop,
            to_state="security_running",
            reason="Running gitleaks delivery gate.",
            trigger="gitleaks",
            actor=actor,
            context_patch=self._durable_run_patch(loop, {"status": "security_running"}),
            thread_id=thread_id,
        )
        try:
            gitleaks = git.gitleaks_scan(
                project_id,
                workspace_id=workspace["id"],
                workspace_path=workspace["path"],
            )
        except TypeError as error:
            if "unexpected keyword" not in str(error):
                raise
            gitleaks = git.gitleaks_scan(project_id)
        if gitleaks.get("status") != "completed" or bool(gitleaks.get("deliveryBlocked")):
            reason = str(gitleaks.get("reason") or "gitleaks blocked delivery.")
            return self._block_run(
                loop, stage="gitleaks", reason=reason, actor=actor, details=gitleaks, thread_id=thread_id
            )

        diff_ref = _diff_ref_from_review(review)
        diff_summary = _diff_summary_from_review(review)
        qa_results = runtime_result.get("qaResults") if isinstance(runtime_result.get("qaResults"), list) else []
        security_evidence = self._record_run_evidence(
            project_id=project_id,
            loop_id=loop["id"],
            stage="gitleaks",
            status="completed",
            reason=str(gitleaks.get("reason") or "gitleaks passed."),
            details=gitleaks,
            workspace_id=workspace["id"],
            diff_refs=[diff_ref],
            diff_summary=diff_summary,
            test_results=[
                *qa_results,
                {
                    "command": "gitleaks",
                    "status": "passed",
                    "metadata": {
                        "workspaceId": workspace["id"],
                        "findingCount": (gitleaks.get("gitleaks") or {}).get("findingCount", 0),
                    },
                },
            ],
            tool_calls=gitleaks.get("toolCalls") or [],
            policy_decisions=[
                {"id": item} for item in (gitleaks.get("policyDecisionIds") or []) if str(item).strip()
            ],
        )
        approval_evidence_ids = [*evidence_ids, security_evidence["id"]]
        approval_job = self.jobs.create_job(
            project_id=project_id,
            kind="product_loop_delivery_approval",
            status="approval_required",
            payload={
                "loopId": loop["id"],
                "workspaceId": workspace["id"],
                "evidenceRefs": approval_evidence_ids,
                "diffRefs": [diff_ref],
                "changedFiles": review["changedFiles"],
            },
        )["job"]
        approval_action = self.jobs.create_action_request(
            job_id=approval_job["id"],
            project_id=project_id,
            action_type="product_loop.approve_delivery",
            risk_level="medium",
            command="approve product loop delivery",
            payload={
                "loopId": loop["id"],
                "workspaceId": workspace["id"],
                "evidenceRefs": approval_evidence_ids,
                "diffRefs": [diff_ref],
                "changedFiles": review["changedFiles"],
            },
            reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
        )
        approval = {
            "status": "available",
            "jobId": approval_job["id"],
            "actionRequestId": approval_action["id"],
            "evidenceRefs": approval_evidence_ids,
            "diffRefs": [diff_ref],
        }
        loop = self._transition_run_state(
            loop,
            to_state="review_ready",
            reason="Runtime, QA and gitleaks evidence are ready for review.",
            trigger="review_ready",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop,
                {"status": "review_ready", "gitleaks": gitleaks, "review": review, "approval": approval},
                evidence_package_ids=[security_evidence["id"]],
            ),
            thread_id=thread_id,
        )
        loop = self._transition_run_state(
            loop,
            to_state="awaiting_approval",
            reason="Evidence-backed Product Loop result awaits operator approval.",
            trigger="awaiting_approval",
            actor=actor,
            context_patch=self._durable_run_patch(
                loop, {"status": "awaiting_approval", "approval": approval}
            ),
            thread_id=thread_id,
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type="approval_required",
            agent_role="aido_lead",
            payload={"loopId": loop["id"], **approval},
        )
        return self._run_result(
            loop,
            status="awaiting_approval",
            reason="Evidence-backed Product Loop result awaits operator approval.",
            evidence_package=security_evidence,
        )

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
                acceptance_criteria = [
                    str(item).strip()
                    for item in story_payload.get("acceptanceCriteria") or []
                    if str(item).strip()
                ]
                if not acceptance_criteria:
                    raise ProductLoopTransitionError(
                        "Feedback action change_scope requires story.acceptanceCriteria."
                    )
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
