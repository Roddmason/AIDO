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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.assessment_runner import ProjectAssessmentRunner
from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.developer_agent_contract import (
    DEVELOPER_AGENT_CLI_RUNTIMES,
    DEVELOPER_AGENT_ID,
    DEVELOPER_AGENT_MODEL_RUNTIMES,
)
from local_control_center.agents.product_owner_agent import (
    ProductOwnerAgent,
    ProductOwnerAgentRunner,
    ProductOwnerOutputValidationError,
    persist_product_owner_backlog,
)
from local_control_center.agents.product_owner_agent_contract import (
    PRODUCT_OWNER_AGENT_CLI_RUNTIMES,
    PRODUCT_OWNER_AGENT_ID,
    PRODUCT_OWNER_AGENT_MODEL_RUNTIMES,
    PRODUCT_OWNER_AGENT_RUNTIME_ORDER,
)
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.runtime_failover import (
    MAX_FAILOVER_ATTEMPTS,
    FailureClass,
    classify_runtime_failure,
    exclusion_for,
    is_affordable_candidate,
    should_failover,
)
from local_control_center.agents.security_agent import SecurityAgentRunner
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.backlog.story_spec import build_story_spec, render_story_spec_prompt
from local_control_center.backlog.technical_lead_planner import TechnicalLeadPlanner
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.git_workspace.service import GitWorkspaceService
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.product_loop.intent_classifier import IntentClassificationInput, IntentClassifier
from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.repository import RemediationActionsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import iso_after_seconds, utc_now
from local_control_center.team_scheduler.scheduler import MODES, RISKS, schedule_team
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.threads.similarity import SIMILARITY_ACTIONS, ThreadMemoryService
from local_control_center.workspaces_projects.git_worktrees import (
    capture_git_diff,
    commit_workspace_changes,
)
from local_control_center.workspaces_projects.repository import (
    WorkspaceConflictError,
    WorkspaceIsolationError,
    WorkspacesRepository,
)

from .delivery import ProductLoopDeliveryService
from .models import FEEDBACK_ACTION_VALUES, FEEDBACK_CLASSIFICATION_VALUES
from .repository import ProductLoopRepository, stable_task_suffix

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
# Tope de comandos QA fallidos detallados en el feedback de rework; el resto se reporta como conteo.
REWORK_FEEDBACK_COMMAND_LIMIT = 8
# Tope del texto de instrucción del developer (se reenvía por ronda de rework y por failover).
INSTRUCTION_PROMPT_LIMIT_CHARS = 20_000
AWAITING_FEEDBACK_STATE = "awaiting_feedback"
TERMINAL_STATES = {DELIVERED_STATE, CANCELLED_STATE}
# Landing states of an abort itself: checking for cancellation again here would recurse forever.
_ABORT_EXEMPT_STATES = {BLOCKED_STATE, CANCELLED_STATE}
_RESUMABLE_STATES = {state for state in PRODUCT_LOOP_STATES if state not in TERMINAL_STATES | {BLOCKED_STATE}}
_TRUSTED_RESOURCE_APPROVAL_METADATA_KEYS = frozenset(
    {"retryOfLoopId", "planOnlyOfLoopId", "continueOfLoopId", "remediationActionId"}
)

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
    "iteration_planning": {"branch_ready", "executing", "blocked", "cancelled"},
    "executing": {"qa_running", "blocked", "cancelled"},
    "qa_running": {"security_running", "reworking", "blocked", "cancelled"},
    "security_running": {"quality_review", "review_ready", "blocked", "cancelled"},
    "review_ready": {"awaiting_approval", "reworking", "blocked", "cancelled"},
    "quality_review": {"review_ready", "awaiting_approval", "reworking", "blocked", "cancelled"},
    "awaiting_approval": {"delivered", "reworking", "awaiting_feedback", "blocked", "cancelled"},
    "awaiting_feedback": {"reworking", "executing", "blocked", "cancelled"},
    "reworking": {"executing", "blocked", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
    "blocked": set(_RESUMABLE_STATES) | {CANCELLED_STATE},
}

STOP_CONDITIONS = ("budget_exhausted", "deadline_exceeded", "state_timeout", "max_rework_reached")
# Contrato de salida de _run_user_message: todo status que un run puede retornar. El worker y los
# gates de taxonomía (test_product_loop_result_taxonomy.py) enumeran contra esta tupla, así que un
# status nuevo obliga a decidir su ruteo (hilo/evento/veredicto) en el mismo commit.
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
# Statuses de etapa que representan un cierre sano: producen veredicto de evidencia "passed".
# Cualquier status fuera de este set cae al lado bloqueado (severidad high) a propósito.
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
_TECHNICAL_TEAM_SCOPES = {
    "api",
    "auth",
    "backend",
    "ci",
    "data",
    "database",
    "deploy",
    "devops",
    "external",
    "frontend",
    "infra",
    "migration",
    "ml",
    "mobile",
    "payments",
    "pii",
    "schema",
    "security",
    "ui",
    "web",
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


def _bounded_instruction(text: str, limit: int = INSTRUCTION_PROMPT_LIMIT_CHARS) -> str:
    """Acota la instrucción del developer cortando el MEDIO del texto con marcador.

    El inicio (qué se pide) y el final (últimos matices del operador) son las partes con más señal;
    un mensaje desbordado se re-paga por cada ronda de rework y por cada failover de runtime.
    """
    if len(text) <= limit:
        return text
    half = (limit - 40) // 2
    return f"{text[:half]}\n[... instruction truncated ...]\n{text[-half:]}"


class ProductLoopTransitionError(ValueError):
    """Se lanza ante un estado desconocido, una transición no permitida o un desfase de versión."""


class ProductLoopStopConditionError(ValueError):
    """Se lanza cuando una política de parada impide la transición (p. ej. máximo de rework alcanzado)."""


class _ProductLoopCancelled(RuntimeError):
    """Control de flujo interno: el operador canceló el run y el loop debe abortar en el límite de etapa.

    Nunca escapa de ``run_user_message``, que la traduce al estado terminal ``cancelled``.
    """

    def __init__(self, loop_id: str, thread_id: str | None) -> None:
        super().__init__(f"Product Loop {loop_id} was cancelled by the operator.")
        self.loop_id = loop_id
        self.thread_id = thread_id


@dataclass
class _UserMessageRun:
    """Estado mutable compartido entre las fases privadas de ``_run_user_message``.

    Los primeros campos son los argumentos del run; el resto lo llena cada fase en orden y lo
    consumen las siguientes. No es contrato público: existe solo para que las fases puedan
    cortar el pipeline con retornos señalizados sin arrastrar firmas gigantes.
    """

    project_id: str
    message: str
    actor: str
    root: str | Path | None = None
    title: str | None = None
    preferred_runtime: str | None = None
    qa_commands: list[Any] | None = None
    run_metadata: dict[str, Any] | None = None
    session_id: str | None = None
    thread_id: str | None = None
    runtime_runner: Any | None = None
    git_service: Any | None = None
    product_owner_runner: Any | None = None
    assessment_runner: Any | None = None
    technical_lead_runner: Any | None = None

    message_text: str = ""
    effective_root: Path | None = None
    resolved_title: str = ""
    request_meta: dict[str, Any] = field(default_factory=dict)
    plan_only: bool = False
    thread: dict[str, Any] = field(default_factory=dict)
    loop: dict[str, Any] = field(default_factory=dict)
    git: Any = None
    git_state: dict[str, Any] = field(default_factory=dict)
    product_owner: Any = None
    product_owner_task_id: str = ""
    product_owner_resource_decision: dict[str, Any] = field(default_factory=dict)
    product_owner_selected_resource: dict[str, Any] = field(default_factory=dict)
    product_owner_preferred_runtime: str | None = None
    assessment_result: dict[str, Any] | None = None
    product_owner_workspace: dict[str, Any] = field(default_factory=dict)
    product_owner_result: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    product_owner_status: str = ""
    brief: dict[str, Any] = field(default_factory=dict)
    product_owner_output_record: dict[str, Any] = field(default_factory=dict)
    pending_thread_decisions: list[dict[str, Any]] = field(default_factory=list)
    po_artifact_ids: list[str] = field(default_factory=list)
    po_evidence: dict[str, Any] = field(default_factory=dict)
    product_owner_context: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    backlog_artifact: dict[str, Any] = field(default_factory=dict)
    agent_tasks: list[dict[str, Any]] = field(default_factory=list)
    team_schedule: dict[str, Any] = field(default_factory=dict)
    team_assignments: list[dict[str, Any]] = field(default_factory=list)
    runtime: Any = None
    execution_resource: dict[str, Any] = field(default_factory=dict)
    effective_preferred_runtime: str | None = None
    readiness: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    workspace: dict[str, Any] = field(default_factory=dict)
    runtime_result: dict[str, Any] = field(default_factory=dict)
    runtime_status: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    qa_results: list[Any] = field(default_factory=list)
    qa_verdict: str = ""
    rework_round: int = 0
    rework_feedback: str | None = None
    base_task_id: str = ""
    gitleaks: dict[str, Any] = field(default_factory=dict)
    diff_ref: dict[str, Any] = field(default_factory=dict)
    security_evidence: dict[str, Any] = field(default_factory=dict)
    resource_learning: dict[str, Any] = field(default_factory=dict)


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
        self.routing_profiles = RoutingProfileStore(connection)
        self.evidence = EvidenceRepository(connection)
        self.events = EventBus(connection)
        self.jobs = JobsRepository(connection)
        # Set only for the duration of a `run_user_message` that was given a cancellation signal.
        self._should_abort: Callable[[], bool] | None = None

    @staticmethod
    def _state_deadline(state: str, timeouts: dict[str, Any] | None, now: str) -> str | None:
        seconds = (timeouts or {}).get(state)
        if seconds is None:
            return None
        return iso_after_seconds(now, float(seconds))

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
        durable = {**self._durable_run_context(loop), **redact_secrets(updates)}
        ids = [str(item) for item in durable.get("evidencePackageIds", []) if str(item).strip()]
        for evidence_id in evidence_package_ids or []:
            if evidence_id and evidence_id not in ids:
                ids.append(evidence_id)
        durable["evidencePackageIds"] = ids
        durable["updatedAt"] = utc_now()
        return {"durableRun": durable}

    def _delivery_approval_ref(self, loop: dict[str, Any]) -> dict[str, str] | None:
        approval = self._durable_run_context(loop).get("approval")
        if not isinstance(approval, dict):
            return None
        job_id = str(approval.get("jobId") or "").strip()
        action_id = str(approval.get("actionRequestId") or "").strip()
        if not job_id or not action_id:
            return None
        return {"jobId": job_id, "actionRequestId": action_id}

    def _resolve_delivery_approval_action(
        self,
        *,
        loop: dict[str, Any],
        decision: str,
        reason: str,
        actor: str,
    ) -> dict[str, Any] | None:
        ref = self._delivery_approval_ref(loop)
        if ref is None or decision not in {"accept", "request_changes"}:
            return None
        job_id = ref["jobId"]
        action_id = ref["actionRequestId"]
        try:
            action = self.jobs.get_action_request(action_id)
        except KeyError as error:
            raise ProductLoopTransitionError(f"Delivery approval action not found: {action_id}.") from error
        if action["jobId"] != job_id:
            raise ProductLoopTransitionError(
                f"Delivery approval action {action_id} is not scoped to job {job_id}."
            )
        if action["projectId"] != loop["projectId"]:
            raise ProductLoopTransitionError(
                f"Delivery approval action {action_id} is not scoped to product loop project {loop['projectId']}."
            )
        if action["actionType"] not in {
            "product_loop.approve_delivery",
            "job.product_loop_delivery_approval",
        }:
            raise ProductLoopTransitionError(
                f"Delivery approval action {action_id} has unexpected type {action['actionType']}."
            )
        if action["status"] == "pending" and decision == "accept":
            action = self.jobs.approve_action(job_id, action_id, reason=reason, actor=actor)["actionRequest"]
        elif action["status"] == "pending" and decision == "request_changes":
            action = self.jobs.deny_action(job_id, action_id, reason=reason, actor=actor)["actionRequest"]
        return {
            "type": "resolve_delivery_approval_action",
            "decision": decision,
            "jobId": job_id,
            "actionRequestId": action_id,
            "status": action["status"],
            "decidedBy": action.get("decidedBy"),
            "decidedAt": action.get("decidedAt"),
        }

    def _record_delivery_approval_decision(
        self, loop: dict[str, Any], effect: dict[str, Any] | None
    ) -> dict[str, Any]:
        if effect is None:
            return loop
        durable = self._durable_run_context(loop)
        approval = dict(durable.get("approval") or {})
        approval.update(
            {
                "status": effect["status"],
                "decision": effect["decision"],
                "jobId": effect["jobId"],
                "actionRequestId": effect["actionRequestId"],
                "decidedBy": effect.get("decidedBy"),
                "decidedAt": effect.get("decidedAt"),
            }
        )
        durable["approval"] = approval
        durable["updatedAt"] = utc_now()
        return self.repository.update_loop_context(
            loop["id"], context={**loop["context"], "durableRun": durable}
        )

    def _land_delivered_work(self, loop: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Aterriza la rama de trabajo del loop recién entregado según ``project.git.integrationMode``.

        Best-effort: un aterrizaje bloqueado o fallido se persiste como evidencia en el contexto
        durable (``landing``) y como efecto, pero jamás revierte la entrega ya aprobada.
        """
        durable = self._durable_run_context(loop)
        workspace_id = str(durable.get("workspaceId") or "").strip()
        if not workspace_id:
            return loop, None
        project_id = str(loop["projectId"])
        root = self.root if self.root is not None else Path(self._queue_root(project_id) or ".")
        try:
            landing = ProductLoopDeliveryService(self.connection, root=root).land(
                project_id=project_id,
                workspace_id=workspace_id,
                loop_id=str(loop["id"]),
                title=str(loop.get("title") or ""),
                loop=loop,
            )
        except Exception as error:
            landing = {"status": "landing_failed", "reason": redact_secrets(str(error))}
        durable["landing"] = redact_secrets(landing)
        durable["updatedAt"] = utc_now()
        loop = self.repository.update_loop_context(
            loop["id"], context={**loop["context"], "durableRun": durable}
        )
        return loop, {
            "type": "delivery_landing",
            "status": str(landing.get("status") or "unknown"),
            "effectiveMode": landing.get("effectiveMode"),
            "degradedFrom": landing.get("degradedFrom"),
            "baseBranch": landing.get("baseBranch"),
        }

    def _delivery_thread_id(self, loop: dict[str, Any]) -> str:
        thread = self._durable_run_context(loop).get("thread")
        if not isinstance(thread, dict):
            return ""
        return str(thread.get("projectThreadId") or "").strip()

    def _queue_root(self, project_id: str) -> str | None:
        if self.root is not None:
            return str(self.root)
        try:
            project = ProjectsRepository(self.connection).get_project(project_id)
        except KeyError:
            return None
        path = str(project.get("path") or "").strip()
        if not path:
            return None
        return str(Path(path).resolve(strict=False))

    @staticmethod
    def _source_thread_message(
        *,
        threads: ThreadsRepository,
        thread_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        if message_id:
            return threads.get_message(message_id)
        user_messages = [message for message in threads.list_messages(thread_id) if message["kind"] == "user"]
        return user_messages[-1] if user_messages else None

    def _queue_delivery_feedback_continuation(
        self,
        *,
        loop: dict[str, Any],
        feedback_id: str,
        feedback: str,
        actor: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        durable = self._durable_run_context(loop)
        thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
        request_meta = (
            strip_untrusted_resource_cost_policy_metadata(durable.get("requestMeta"))
            if isinstance(durable.get("requestMeta"), dict)
            else {}
        )
        thread_id = str(thread_ref.get("projectThreadId") or "").strip()
        if not thread_id:
            raise ProductLoopTransitionError("Feedback action continue requires a Product Loop thread.")

        threads = ThreadsRepository(self.connection)
        thread = threads.get_thread(thread_id)
        if thread["projectId"] != loop["projectId"]:
            raise ProductLoopTransitionError("Thread project does not match the Product Loop project.")
        if thread["status"] in {"queued", "running"}:
            raise ProductLoopTransitionError(
                f"Thread is already {thread['status']}; continue would create concurrent execution."
            )

        message_id = str(thread_ref.get("messageId") or request_meta.get("messageId") or "").strip()
        message = str(durable.get("message") or "").strip()
        source_message = self._source_thread_message(
            threads=threads,
            thread_id=thread_id,
            message_id=message_id,
        )
        if source_message is not None:
            message_id = source_message["id"]
            message = message or str(source_message["content"] or "").strip()
        if not message_id or not message:
            raise ProductLoopTransitionError("Feedback action continue requires the original thread message.")

        root = self._queue_root(loop["projectId"])
        if not root:
            raise ProductLoopTransitionError("Project root is required to queue Product Loop continuation.")

        queued_at = utc_now()
        plan_only = bool(request_meta.get("planOnly") or request_meta.get("plan_only"))
        approved_resource_selections = self._approved_resource_selections_from_durable(durable)
        run_metadata = {
            **request_meta,
            "messageId": message_id,
            "planOnly": plan_only,
            "approvedResourceSelections": approved_resource_selections,
            "continueOfLoopId": loop["id"],
            "continueReason": feedback,
            "feedbackId": feedback_id,
            "continueQueuedAt": queued_at,
        }
        job = self.jobs.create_job(
            project_id=loop["projectId"],
            kind="thread.product_loop.run",
            payload={
                "threadId": thread_id,
                "messageId": message_id,
                "projectId": loop["projectId"],
                "message": message,
                "title": thread["title"] or loop["title"],
                "root": root,
                "decision": request_meta.get("decision"),
                "teamPlan": request_meta.get("teamPlan"),
                "planOnly": plan_only,
                "approvedResourceSelections": approved_resource_selections,
                "runMetadata": run_metadata,
                "continueOfLoopId": loop["id"],
                "continueReason": feedback,
                "feedbackId": feedback_id,
                "continueQueuedAt": queued_at,
            },
            idempotency_key=f"product-loop-continue:{loop['id']}:{feedback_id}",
        )["job"]
        continuation = {
            "status": "queued",
            "jobId": job["id"],
            "threadId": thread_id,
            "messageId": message_id,
            "feedbackId": feedback_id,
            "queuedAt": queued_at,
        }
        durable = {
            **durable,
            "status": "continuation_queued",
            "continuation": continuation,
            "updatedAt": queued_at,
        }
        loop = self.repository.update_loop_context(
            loop["id"],
            context={**loop["context"], "durableRun": durable},
        )
        threads.set_status(thread_id, "queued")
        threads.record_event(
            thread_id=thread_id,
            type="run_queued",
            agent_role=actor,
            payload={
                "loopId": loop["id"],
                "jobId": job["id"],
                "messageId": message_id,
                "status": job["status"],
                "continueOfLoopId": loop["id"],
                "feedbackId": feedback_id,
            },
        )
        return loop, {
            "type": "queue_continuation",
            "jobId": job["id"],
            "threadId": thread_id,
            "messageId": message_id,
            "status": job["status"],
        }

    def _sync_delivery_feedback_thread_state(
        self,
        *,
        loop: dict[str, Any],
        decision: str,
        reason: str,
        actor: str,
    ) -> dict[str, Any] | None:
        thread_id = self._delivery_thread_id(loop)
        if not thread_id:
            return None
        if decision == "accept":
            status = "resolved"
            event_type = "completed"
        elif decision == "request_changes":
            status = "open"
            event_type = "reworking"
        else:
            return None
        self._set_thread_status_best_effort(
            thread_id=thread_id,
            status=status,
            reason=f"Delivery feedback {decision}: {reason}",
        )
        self._record_thread_event(
            thread_id=thread_id,
            event_type=event_type,
            payload={
                "loopId": loop["id"],
                "decision": decision,
                "status": status,
                "reason": reason,
            },
            agent_role=actor,
        )
        return {
            "type": "sync_delivery_feedback_thread_state",
            "threadId": thread_id,
            "decision": decision,
            "status": status,
        }

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
    ) -> dict[str, Any]:
        # Cooperative cancellation checkpoint. Every durable stage advance funnels through here, so a
        # cancel lands within one stage instead of letting the claimed worker run to completion beside
        # its replacement run — which is what would make two Product Loops share a thread.
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
        planned = self.repository.update_loop_context(
            loop["id"], context={**loop["context"], **context_patch}
        )
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
                    options=self._question_options(record) or self._question_options(item),
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
                    options=self._question_options(record) or self._question_options(item),
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

    def _story_specs_for_tasks(self, agent_tasks: list[dict[str, Any]]) -> str | None:
        """Renderiza el spec ejecutable (epica/HU/criterios/roles) de las historias asignadas.

        Devuelve None cuando no hay historias resolubles para no alterar el prompt legado.
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
        return render_story_spec_prompt(specs)

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
        if isinstance(planned, dict):
            specs = planned.get("agent_tasks") or planned.get("agentTasks") or []
            dependency_specs = planned.get("task_dependencies") or planned.get("taskDependencies") or []
        else:
            specs = planned
            dependency_specs = []
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

    def _team_mode(self, request_meta: dict[str, Any]) -> str:
        mode = (
            str(
                request_meta.get("teamMode")
                or request_meta.get("team_mode")
                or request_meta.get("mode")
                or "balanced"
            )
            .strip()
            .lower()
        )
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
        if not scope or not any(scope_name in _TECHNICAL_TEAM_SCOPES for scope_name in scope):
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

    @staticmethod
    def _failed_team_schedule(
        *,
        phase: str,
        reason: str,
        previous_schedule: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        failed = {
            "status": "failed",
            "phase": phase,
            "reason": reason,
        }
        if previous_schedule is not None:
            failed["previousSchedule"] = previous_schedule
        return failed

    @staticmethod
    def _resource_required_capabilities(role_plan: dict[str, Any]) -> list[str]:
        capabilities = {str(item).strip().lower() for item in role_plan.get("capabilities") or []}
        kind = str(role_plan.get("kind") or "").strip().lower()
        required: list[str] = []
        if "code_edit" in capabilities:
            required.append("code")
        if kind == "review" or capabilities & {"security_review", "test_design", "regression", "code_review"}:
            required.append("review")
        return required or ["chat"]

    @staticmethod
    def _resource_context_tokens_estimate(request_meta: dict[str, Any]) -> int:
        for key in (
            "contextTokensEstimate",
            "context_tokens_estimate",
            "estimatedTokens",
            "estimated_tokens",
        ):
            value = request_meta.get(key)
            if value is None or value == "":
                continue
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                continue
        return 0

    @staticmethod
    def _resource_privacy_level(request_meta: dict[str, Any]) -> str:
        value = str(
            request_meta.get("privacyLevel")
            or request_meta.get("privacy_level")
            or request_meta.get("resourcePrivacyLevel")
            or "remote_allowed"
        ).strip()
        return value or "remote_allowed"

    def _resource_role_policy(
        self,
        role: str,
        *,
        fallback_to_developer: bool = True,
    ) -> dict[str, Any]:
        """Normaliza la política mutable del rol para que ResourceManager la aplique fail-closed.

        ``requiresApprovalOverUsd`` es el umbral premium: por encima de él una selección con costo
        conocido exige aprobación humana. Espeja la precedencia del ``ModelRouter`` (umbral explícito
        y, si no lo hay, el cap por tarea). Sin política registrada se asume lo más restrictivo.
        """
        roles = [role]
        if fallback_to_developer and role != "developer":
            roles.append("developer")
        for policy_role in roles:
            try:
                policy = self.routing_profiles.get_role_policy(policy_role)
            except KeyError:
                continue
            threshold = policy.get("requiresApprovalOverUsd")
            if threshold is None:
                threshold = policy.get("maxCostPerTaskUsd")
            preferred_resources = [
                preference
                for preference in [
                    *(policy.get("preferred") or []),
                    *(policy.get("fallback") or []),
                    *(policy.get("escalation") or []),
                ]
                if isinstance(preference, dict)
            ]
            preferred_provider_ids: list[str] = []
            for preference in preferred_resources:
                provider_id = str(preference.get("provider") or "").strip()
                if provider_id and provider_id not in preferred_provider_ids:
                    preferred_provider_ids.append(provider_id)
            return {
                "rolePolicyId": str(policy.get("id") or policy_role),
                "routingProfileId": str(policy.get("routingProfileId") or ""),
                "freeTierOnly": str(policy.get("routingProfileId") or "") == "free_tier",
                "allowUnknownCost": bool(policy.get("allowUnknownCost", False)),
                "requireApprovalForUnknownCost": bool(policy.get("requireApprovalForUnknownCost", True)),
                "requiresApprovalOverUsd": float(threshold) if threshold is not None else None,
                "maxTokensPerRun": int(policy.get("maxTokensPerRun") or 0) or None,
                "allowRemote": bool(policy.get("allowRemote", False)),
                "allowLocal": bool(policy.get("allowLocal", False)),
                "allowCli": bool(policy.get("allowCli", False)),
                "allowApi": bool(policy.get("allowApi", False)),
                "preferredProviderIds": preferred_provider_ids,
                "preferredResources": preferred_resources,
                "blockedResources": [item for item in policy.get("blocked") or [] if isinstance(item, dict)],
            }
        return {
            "rolePolicyId": None,
            "routingProfileId": None,
            "freeTierOnly": False,
            "allowUnknownCost": False,
            "requireApprovalForUnknownCost": True,
            "requiresApprovalOverUsd": None,
            "maxTokensPerRun": None,
            "allowRemote": False,
            "allowLocal": False,
            "allowCli": False,
            "allowApi": False,
            "preferredProviderIds": [],
            "preferredResources": [],
            "blockedResources": [],
        }

    @staticmethod
    def _public_resource_decision(decision: dict[str, Any]) -> dict[str, Any]:
        return redact_secrets(
            {
                "routingDecisionId": decision.get("routingDecisionId"),
                "selected": decision.get("selected"),
                "reviewerSelection": decision.get("reviewerSelection"),
                "localVsRemote": decision.get("localVsRemote"),
                "costTier": decision.get("costTier"),
                "multiModelQuorum": decision.get("multiModelQuorum"),
                "contextCompression": decision.get("contextCompression"),
                "maxTokens": decision.get("maxTokens"),
                "budgetStop": decision.get("budgetStop"),
                "approvalRequired": decision.get("approvalRequired"),
                "approvalSatisfied": bool(decision.get("approvalSatisfied")),
                "estimatedCostUsd": decision.get("estimatedCostUsd"),
                "usageStatus": decision.get("usageStatus"),
                "decisionReason": decision.get("decisionReason"),
                "scoreBreakdown": decision.get("scoreBreakdown") or {},
                "policyResult": decision.get("policyResult") or {},
                "candidates": decision.get("candidates") or [],
                "rejected": decision.get("rejected") or [],
            }
        )

    @staticmethod
    def _approved_resource_selection_for(
        *,
        role: str,
        selected: dict[str, Any] | None,
        request_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not selected:
            return None
        approvals = request_meta.get("approvedResourceSelections")
        if not isinstance(approvals, list):
            return None
        for approval in approvals:
            if not isinstance(approval, dict):
                continue
            if str(approval.get("role") or "") != role:
                continue
            if all(
                str(approval.get(key) or "") == str(selected.get(key) or "")
                for key in ("providerId", "model", "runtime")
            ):
                return redact_secrets(approval)
        return None

    @staticmethod
    def _same_resource_selection(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
        return all(
            str(current.get(key) or "") == str(candidate.get(key) or "")
            for key in ("role", "providerId", "model", "runtime")
        )

    @staticmethod
    def _source_resource_approval_loop_id(request_meta: dict[str, Any]) -> str:
        return str(
            request_meta.get("retryOfLoopId")
            or request_meta.get("planOnlyOfLoopId")
            or request_meta.get("continueOfLoopId")
            or ""
        ).strip()

    def _trusted_remediation_resource_approval_marker(
        self,
        *,
        request_meta: dict[str, Any],
        source_loop: dict[str, Any],
        expected_action_type: str,
    ) -> bool:
        remediation_id = str(request_meta.get("remediationActionId") or "").strip()
        if not remediation_id:
            return False
        try:
            action = RemediationActionsRepository(self.connection).get(remediation_id)
        except KeyError:
            return False
        return (
            action["projectId"] == source_loop["projectId"]
            and action["loopId"] == source_loop["id"]
            and action["actionType"] == expected_action_type
            and action["status"] in {"pending", "resolved"}
        )

    def _trusted_feedback_resource_approval_marker(
        self, *, request_meta: dict[str, Any], source_loop: dict[str, Any]
    ) -> bool:
        feedback_id = str(request_meta.get("feedbackId") or "").strip()
        if not feedback_id:
            return False
        try:
            feedback = self.repository.get_feedback(feedback_id)
        except KeyError:
            return False
        return (
            feedback["projectId"] == source_loop["projectId"]
            and feedback["loopId"] == source_loop["id"]
            and feedback["action"] == "continue"
            and feedback["status"] == "applied"
        )

    def _has_trusted_resource_approval_metadata(self, request_meta: dict[str, Any]) -> bool:
        approvals = request_meta.get("approvedResourceSelections")
        if not isinstance(approvals, list) or not approvals:
            return False
        source_loop_id = self._source_resource_approval_loop_id(request_meta)
        if not source_loop_id:
            return False
        try:
            source_loop = self.repository.get_loop(source_loop_id)
        except KeyError:
            return False
        source_durable = self._durable_run_context(source_loop)
        resource_approval = (
            source_durable.get("resourceApproval")
            if isinstance(source_durable.get("resourceApproval"), dict)
            else {}
        )
        approved_selections = resource_approval.get("approvedResourceSelections")
        if resource_approval.get("status") != "approved" or not isinstance(approved_selections, list):
            return False
        if not all(
            isinstance(approval, dict)
            and any(
                isinstance(approved, dict) and self._same_resource_selection(approved, approval)
                for approved in approved_selections
            )
            for approval in approvals
        ):
            return False
        if request_meta.get("retryOfLoopId"):
            return self._trusted_remediation_resource_approval_marker(
                request_meta=request_meta,
                source_loop=source_loop,
                expected_action_type="retry_loop",
            )
        if request_meta.get("planOnlyOfLoopId"):
            return self._trusted_remediation_resource_approval_marker(
                request_meta=request_meta,
                source_loop=source_loop,
                expected_action_type="continue_plan_only",
            )
        if request_meta.get("continueOfLoopId"):
            return self._trusted_feedback_resource_approval_marker(
                request_meta=request_meta,
                source_loop=source_loop,
            )
        return False

    def _sanitize_untrusted_resource_approval_metadata(self, request_meta: dict[str, Any]) -> dict[str, Any]:
        if "approvedResourceSelections" not in request_meta:
            return request_meta
        if self._has_trusted_resource_approval_metadata(request_meta):
            return request_meta
        approvals = request_meta.get("approvedResourceSelections")
        sanitized = dict(request_meta)
        sanitized.pop("approvedResourceSelections", None)
        if isinstance(approvals, list) and approvals:
            sanitized["ignoredApprovedResourceSelectionCount"] = len(approvals)
            sanitized["ignoredApprovedResourceSelectionReason"] = (
                "approvedResourceSelections require remediation, retry, plan-only, or continuation provenance."
            )
        return sanitized

    def _resource_decision_with_approval_override(
        self,
        *,
        role: str,
        decision: dict[str, Any],
        request_meta: dict[str, Any],
    ) -> dict[str, Any]:
        if not bool(decision.get("approvalRequired")):
            return decision
        approval = self._approved_resource_selection_for(
            role=role,
            selected=decision.get("selected") if isinstance(decision.get("selected"), dict) else None,
            request_meta=request_meta,
        )
        if approval is None:
            return decision
        policy_result = dict(decision.get("policyResult") or {})
        policy_result["approvalOverride"] = {"approved": True, **approval}
        return {
            **decision,
            # Preserve the immutable routing decision. The durable approval satisfies the gate,
            # but it must not rewrite what AIResourceManager originally required/persisted.
            "approvalSatisfied": True,
            "decisionReason": (
                f"{decision.get('decisionReason') or 'Selected AI resource.'} "
                "Operator approved this exact resource selection for retry."
            ),
            "policyResult": policy_result,
        }

    @staticmethod
    def _resource_decision_has_pending_approval(decision: dict[str, Any]) -> bool:
        if not bool(decision.get("approvalRequired")):
            return False
        if bool(decision.get("approvalSatisfied")):
            return False
        policy_result = decision.get("policyResult") if isinstance(decision.get("policyResult"), dict) else {}
        override = (
            policy_result.get("approvalOverride")
            if isinstance(policy_result.get("approvalOverride"), dict)
            else {}
        )
        return override.get("approved") is not True

    def _team_schedule_with_resource_decisions(
        self,
        *,
        project_id: str,
        loop_id: str,
        request_meta: dict[str, Any],
        team_schedule: dict[str, Any],
        agent_tasks: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        manager = AIResourceManager(self.connection)
        context_tokens = self._resource_context_tokens_estimate(request_meta)
        privacy_level = self._resource_privacy_level(request_meta)
        profiles_by_role = self._profile_by_role()
        enriched_roles: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for role_plan in team_schedule["roles"]:
            role = str(role_plan["role"])
            profile = profiles_by_role.get(role) or {}
            task = self._task_for_assignment(role, agent_tasks)
            resource_policy = self._resource_role_policy(role)
            decision = manager.select_resource(
                AIResourceRequest(
                    project_id=project_id,
                    workflow_run_id=loop_id,
                    agent_id=str(profile.get("id") or role),
                    task_id=task["id"],
                    task_type=f"{role}.{role_plan.get('kind') or 'reason'}",
                    risk_level=str(team_schedule.get("risk") or "medium"),
                    routing_policy=(
                        "economy"
                        if resource_policy["freeTierOnly"]
                        else str(team_schedule.get("mode") or "balanced")
                    ),
                    context_tokens_estimate=context_tokens,
                    required_capabilities=self._resource_required_capabilities(role_plan),
                    privacy_level=privacy_level,
                    budget_remaining_usd=role_plan.get("budgetUsd"),
                    max_tokens=role_plan.get("maxTokens"),
                    preferred_provider_ids=resource_policy["preferredProviderIds"],
                    preferred_resources=resource_policy["preferredResources"],
                    blocked_resources=resource_policy["blockedResources"],
                    context_token_limit=resource_policy["maxTokensPerRun"],
                    role_policy_id=resource_policy["rolePolicyId"],
                    allow_remote=resource_policy["allowRemote"],
                    allow_local=resource_policy["allowLocal"],
                    allow_cli=resource_policy["allowCli"],
                    allow_api=resource_policy["allowApi"],
                    free_tier_only=resource_policy["freeTierOnly"],
                    allow_unknown_cost=resource_policy["allowUnknownCost"],
                    require_approval_for_unknown_cost=resource_policy["requireApprovalForUnknownCost"],
                    require_approval_over_usd=resource_policy["requiresApprovalOverUsd"],
                ),
                record=True,
            )
            decision = self._resource_decision_with_approval_override(
                role=role,
                decision=decision,
                request_meta=request_meta,
            )
            public_decision = self._public_resource_decision(decision)
            enriched_roles.append({**role_plan, "resourceDecision": public_decision})
            if decision.get("selected") is None:
                blockers.append(
                    {
                        "role": role,
                        "taskId": task["id"],
                        "reason": decision.get("decisionReason")
                        or "AIResourceManager did not select a resource.",
                        "decision": public_decision,
                    }
                )
            elif self._resource_decision_has_pending_approval(decision):
                blockers.append(
                    {
                        "role": role,
                        "taskId": task["id"],
                        "reason": "AIResourceManager selected a resource that requires approval before execution.",
                        "decision": public_decision,
                    }
                )
        enriched = {
            **team_schedule,
            "roles": enriched_roles,
            "summary": {
                **dict(team_schedule.get("summary") or {}),
                "resourceDecisionCount": len(enriched_roles),
                "resourceDecisionBlockedCount": len(blockers),
            },
        }
        return enriched, blockers

    @staticmethod
    def _unscheduled_agent_task_roles(
        *, agent_tasks: list[dict[str, Any]], team_schedule: dict[str, Any]
    ) -> list[str]:
        scheduled_roles = {
            str(role_plan.get("role") or "").strip()
            for role_plan in team_schedule.get("roles") or []
            if str(role_plan.get("role") or "").strip()
        }
        task_roles = {
            str(task.get("role") or "").strip() for task in agent_tasks if str(task.get("role") or "").strip()
        }
        return sorted(role for role in task_roles if role not in scheduled_roles)

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

    @staticmethod
    def _runtime_provider_usage(
        runtime_result: dict[str, Any], usage_entry: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        for source in (usage_entry, runtime_result, runtime_result.get("runtimeResult")):
            if not isinstance(source, dict):
                continue
            for key in ("usage", "providerUsage", "rawUsage"):
                value = source.get(key)
                if isinstance(value, dict):
                    return value
        return None

    @staticmethod
    def _runtime_actual_cost_usd(
        runtime_result: dict[str, Any], usage_entry: dict[str, Any] | None = None
    ) -> float | None:
        for source in (usage_entry, runtime_result, runtime_result.get("runtimeResult")):
            if not isinstance(source, dict):
                continue
            for key in ("actualCostUsd", "actual_cost_usd", "costUsd", "cost_usd"):
                value = source.get(key)
                if value is not None and value != "":
                    return float(value)
        return None

    @staticmethod
    def _runtime_latency_ms(
        runtime_result: dict[str, Any], usage_entry: dict[str, Any] | None = None
    ) -> int | None:
        for source in (usage_entry, runtime_result, runtime_result.get("runtimeResult")):
            if not isinstance(source, dict):
                continue
            for key in ("latencyMs", "latency_ms", "durationMs", "duration_ms"):
                value = source.get(key)
                if value is not None and value != "":
                    return int(value)
        return None

    @staticmethod
    def _runtime_bool_metric(
        usage_entry: dict[str, Any] | None,
        *,
        keys: tuple[str, ...],
        default: bool,
    ) -> bool:
        if not isinstance(usage_entry, dict):
            return default
        truthy = {"1", "true", "yes", "passed", "pass", "success", "succeeded", "completed"}
        falsey = {"0", "false", "no", "failed", "fail", "blocked", "not_started"}
        for key in keys:
            value = usage_entry.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            normalized = str(value).strip().lower()
            if normalized in truthy:
                return True
            if normalized in falsey:
                return False
        return default

    @staticmethod
    def _runtime_quality_score_metric(
        usage_entry: dict[str, Any] | None,
        *,
        default: float,
    ) -> float:
        if not isinstance(usage_entry, dict):
            return default
        for key in ("qualityScore", "quality_score", "quality"):
            value = usage_entry.get(key)
            if value is None or value == "":
                continue
            return max(0.0, min(float(value), 1.0))
        return default

    @staticmethod
    def _runtime_resource_usage_entries(runtime_result: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("resourceUsage", "resource_usage", "aiUsage", "ai_usage", "usageObservations"):
            value = runtime_result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _resource_role_matches(role_plan: dict[str, Any], usage_entry: dict[str, Any]) -> bool:
        entry_role = str(usage_entry.get("role") or usage_entry.get("agentRole") or "").strip()
        if not entry_role:
            return True
        return entry_role == str(role_plan.get("role") or "")

    @staticmethod
    def _resource_selection_matches(selected: dict[str, Any], usage_entry: dict[str, Any]) -> bool:
        for selected_key, entry_keys in (
            ("providerId", ("providerId", "provider_id", "provider")),
            ("model", ("model",)),
            ("runtime", ("runtime", "runtimeType", "runtime_type")),
        ):
            entry_value = next(
                (usage_entry.get(key) for key in entry_keys if usage_entry.get(key) not in {None, ""}),
                None,
            )
            if entry_value is not None and str(entry_value) != str(selected.get(selected_key)):
                return False
        return True

    def _is_model_runtime_provider(self, provider_id: str) -> bool:
        """Indica si la cuenta corresponde a un runtime de modelo (no CLI).

        Resuelve por ``provider_family`` y no por ``provider_id``: una cuenta con id propio —por
        ejemplo un gateway ``omniroute`` de familia ``openai_compatible``— debe mapear igual que la
        cuenta homónima de la familia, tal como ya lo hace el camino del ProductOwner. Sin esto la
        selección de recursos elige el gateway y luego el loop lo rechaza por no mapear a un runtime.
        """
        if not provider_id:
            return False
        row = self.connection.execute(
            "SELECT provider_family, api_format FROM provider_accounts WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        if row is None:
            return False
        return (
            str(row["provider_family"] or "") in DEVELOPER_AGENT_MODEL_RUNTIMES
            or str(row["api_format"] or "") == "ollama"
        )

    def _developer_runtime_id_for_resource_selection(self, selected: dict[str, Any]) -> str | None:
        allowed_runtimes = DEVELOPER_AGENT_CLI_RUNTIMES | DEVELOPER_AGENT_MODEL_RUNTIMES
        provider_id = str(selected.get("providerId") or "").strip()
        runtime_id = str(selected.get("runtime") or "").strip()
        if provider_id in allowed_runtimes or self._is_model_runtime_provider(provider_id):
            return provider_id
        if runtime_id in allowed_runtimes:
            return runtime_id
        return None

    def _product_owner_runtime_id_for_resource_selection(self, selected: dict[str, Any]) -> str | None:
        provider_id = str(selected.get("providerId") or "").strip()
        runtime_id = str(selected.get("runtime") or "").strip()
        if provider_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
            return provider_id
        row = self.connection.execute(
            """
            SELECT provider_family, api_format, provider_type
            FROM provider_accounts
            WHERE provider_id = ?
            """,
            (provider_id,),
        ).fetchone()
        if row and (
            str(row["provider_family"] or "") in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
            or str(row["api_format"] or "") == "ollama"
        ):
            return provider_id
        if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
            return runtime_id
        return None

    def _persisted_runtime_order(self) -> list[str]:
        """Devuelve el orden de runtimes elegido por el operador, o vacío si no hay ninguno.

        Sin preferencias registradas ``get_preferences`` levanta ``KeyError``; eso no es un error
        de operación sino la instalación por defecto, así que degrada a lista vacía.
        """
        try:
            preferences = RuntimeConfigRepository(self.connection).get_preferences()
        except KeyError:
            return []
        return [str(item) for item in (preferences.get("runtimeOrder") or []) if str(item).strip()]

    def _product_owner_resource_selection(
        self,
        *,
        project_id: str,
        loop_id: str,
        task_id: str,
        request_meta: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        manager = AIResourceManager(self.connection)
        resource_policy = self._resource_role_policy(
            "product_owner",
            fallback_to_developer=False,
        )
        model_provider_rows = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT provider_id, provider_family, api_format
                FROM provider_accounts
                WHERE provider_type IN ('api', 'gateway', 'local')
                ORDER BY provider_id ASC
                """
            ).fetchall()
            if str(row["provider_family"] or "") in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
            or str(row["api_format"] or "") == "ollama"
        ]
        model_provider_ids = {str(row["provider_id"]) for row in model_provider_rows}
        allowed_provider_ids = sorted(PRODUCT_OWNER_AGENT_CLI_RUNTIMES | model_provider_ids)
        preferred_provider_ids: list[str] = []
        ordered_contract_providers: list[str] = []
        for runtime_family in PRODUCT_OWNER_AGENT_RUNTIME_ORDER:
            if runtime_family in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
                ordered_contract_providers.append(runtime_family)
                continue
            ordered_contract_providers.extend(
                str(row["provider_id"])
                for row in model_provider_rows
                if str(row["provider_family"] or "") == runtime_family
                or (runtime_family == "ollama" and str(row["api_format"] or "") == "ollama")
            )
        # La preferencia persistida va primero: es lo que escribe la remediación switch_runtime, que
        # hasta ahora reportaba éxito sobre un valor que nadie leía.
        for provider_id in [
            *self._persisted_runtime_order(),
            *resource_policy["preferredProviderIds"],
            *ordered_contract_providers,
        ]:
            if provider_id in allowed_provider_ids and provider_id not in preferred_provider_ids:
                preferred_provider_ids.append(provider_id)
        # El rank duro del selector respeta la misma precedencia: runtime persistido (switch_runtime)
        # primero, luego las entradas provider+model de la política del rol. El orden de contrato
        # sigue siendo solo desempate provider-level vía preferred_provider_ids.
        preferred_resources = [
            *({"provider": provider_id, "model": ""} for provider_id in self._persisted_runtime_order()),
            *resource_policy["preferredResources"],
        ]
        decision = manager.select_resource(
            AIResourceRequest(
                project_id=project_id,
                workflow_run_id=loop_id,
                agent_id=PRODUCT_OWNER_AGENT_ID,
                task_id=task_id,
                task_type="product_owner.discovery",
                risk_level=str(request_meta.get("risk") or "medium"),
                routing_policy=(
                    "economy" if resource_policy["freeTierOnly"] else self._team_mode(request_meta)
                ),
                context_tokens_estimate=self._resource_context_tokens_estimate(request_meta),
                required_capabilities=["chat"],
                allowed_provider_ids=allowed_provider_ids,
                preferred_provider_ids=preferred_provider_ids,
                preferred_resources=preferred_resources,
                blocked_resources=resource_policy["blockedResources"],
                context_token_limit=resource_policy["maxTokensPerRun"],
                role_policy_id=resource_policy["rolePolicyId"],
                allow_remote=resource_policy["allowRemote"],
                allow_local=resource_policy["allowLocal"],
                allow_cli=resource_policy["allowCli"],
                allow_api=resource_policy["allowApi"],
                free_tier_only=resource_policy["freeTierOnly"],
                privacy_level=self._resource_privacy_level(request_meta),
                allow_unknown_cost=resource_policy["allowUnknownCost"],
                require_approval_for_unknown_cost=resource_policy["requireApprovalForUnknownCost"],
                require_approval_over_usd=resource_policy["requiresApprovalOverUsd"],
            ),
            record=True,
        )
        decision = self._resource_decision_with_approval_override(
            role="product_owner",
            decision=decision,
            request_meta=request_meta,
        )
        public_decision = self._public_resource_decision(decision)
        selected = (
            public_decision.get("selected") if isinstance(public_decision.get("selected"), dict) else None
        )
        runtime_id = self._product_owner_runtime_id_for_resource_selection(selected or {})
        if selected is None:
            return public_decision, {
                "role": "product_owner",
                "taskId": task_id,
                "reason": decision.get("decisionReason") or "AIResourceManager did not select a resource.",
                "decision": public_decision,
            }
        if self._resource_decision_has_pending_approval(public_decision):
            return public_decision, {
                "role": "product_owner",
                "taskId": task_id,
                "reason": "AIResourceManager selected a resource that requires approval before execution.",
                "decision": public_decision,
            }
        if runtime_id is None:
            return public_decision, {
                "role": "product_owner",
                "taskId": task_id,
                "reason": "Selected AI resource does not map to a ProductOwnerAgent runtime.",
                "decision": public_decision,
            }
        return public_decision, None

    def _developer_execution_resource(self, team_schedule: dict[str, Any]) -> dict[str, Any]:
        roles = [
            role_plan
            for role_plan in team_schedule.get("roles") or []
            if isinstance((role_plan.get("resourceDecision") or {}).get("selected"), dict)
            and (role_plan.get("resourceDecision") or {}).get("selected")
        ]
        execution_roles = [
            role_plan
            for role_plan in roles
            if role_plan.get("kind") in {"build", "review"}
            or "code_edit" in (role_plan.get("capabilities") or [])
        ]
        for role_plan in execution_roles or roles:
            decision = role_plan.get("resourceDecision") or {}
            selected = decision.get("selected") or {}
            preferred_runtime = self._developer_runtime_id_for_resource_selection(selected)
            if not preferred_runtime:
                continue
            return redact_secrets(
                {
                    "role": role_plan.get("role"),
                    "providerId": selected.get("providerId"),
                    "model": selected.get("model"),
                    "runtime": selected.get("runtime"),
                    "preferredRuntime": preferred_runtime,
                    "decisionReason": decision.get("decisionReason"),
                    "estimatedCostUsd": decision.get("estimatedCostUsd"),
                    "usageStatus": decision.get("usageStatus"),
                }
            )
        return {}

    def _security_execution_resource(self, team_schedule: dict[str, Any]) -> dict[str, Any]:
        """Devuelve el runtime de modelo que el schedule eligió para el rol de seguridad, si lo hay.

        El análisis de modelo del SecurityAgent es asistencia opcional sobre veredictos que siguen
        siendo deterministas, así que solo se activa cuando la selección de recursos del rol ya
        resolvió un runtime de modelo ejecutable; sin él, el agente corre solo sus scanners.
        """
        for role_plan in team_schedule.get("roles") or []:
            capabilities = set(role_plan.get("capabilities") or [])
            role = str(role_plan.get("role") or "")
            if "security_review" not in capabilities and not role.startswith("security"):
                continue
            selected = (role_plan.get("resourceDecision") or {}).get("selected") or {}
            provider_id = str(selected.get("providerId") or "").strip()
            if not self._is_model_runtime_provider(provider_id):
                continue
            return {"preferredRuntime": provider_id, "model": selected.get("model")}
        return {}

    def _developer_execution_resource_mapping_blockers(
        self, team_schedule: dict[str, Any]
    ) -> list[dict[str, Any]]:
        roles = [
            role_plan
            for role_plan in team_schedule.get("roles") or []
            if isinstance((role_plan.get("resourceDecision") or {}).get("selected"), dict)
            and (role_plan.get("resourceDecision") or {}).get("selected")
        ]
        execution_roles = [
            role_plan
            for role_plan in roles
            if role_plan.get("kind") in {"build", "review"}
            or "code_edit" in (role_plan.get("capabilities") or [])
        ]
        targets = execution_roles or roles
        if not targets:
            return []
        if any(
            self._developer_runtime_id_for_resource_selection(
                (role_plan.get("resourceDecision") or {}).get("selected") or {}
            )
            for role_plan in targets
        ):
            return []
        return [
            redact_secrets(
                {
                    "role": role_plan.get("role"),
                    "reason": "Selected AI resource does not map to a DeveloperAgent runtime.",
                    "decision": role_plan.get("resourceDecision") or {},
                }
            )
            for role_plan in targets
        ]

    def _resource_observation_targets(
        self,
        *,
        team_schedule: dict[str, Any],
        runtime_result: dict[str, Any],
    ) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        roles = [
            role_plan
            for role_plan in team_schedule.get("roles") or []
            if isinstance((role_plan.get("resourceDecision") or {}).get("selected"), dict)
            and (role_plan.get("resourceDecision") or {}).get("selected")
        ]
        usage_entries = self._runtime_resource_usage_entries(runtime_result)
        if usage_entries:
            targets: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            for entry in usage_entries:
                for role_plan in roles:
                    selected = (role_plan.get("resourceDecision") or {}).get("selected") or {}
                    if self._resource_role_matches(role_plan, entry) and self._resource_selection_matches(
                        selected,
                        entry,
                    ):
                        targets.append((role_plan, entry))
                        break
            return targets
        execution_roles = [
            role_plan
            for role_plan in roles
            if role_plan.get("kind") in {"build", "review"}
            or "code_edit" in (role_plan.get("capabilities") or [])
        ]
        fallback = execution_roles or roles
        return [(fallback[0], None)] if fallback else []

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
        selected = decision.get("selected") or {}
        provider_id = str(selected.get("providerId") or "")
        model = str(selected.get("model") or "")
        runtime = str(selected.get("runtime") or "")
        if not provider_id or not model or not runtime:
            return None
        effective_success = self._runtime_bool_metric(
            usage_entry,
            keys=("success", "succeeded", "passed", "status"),
            default=success,
        )
        effective_rework = self._runtime_bool_metric(
            usage_entry,
            keys=("rework", "requiresRework", "requires_rework"),
            default=rework,
        )
        effective_quality_score = self._runtime_quality_score_metric(
            usage_entry,
            default=quality_score,
        )
        latency_ms = self._runtime_latency_ms(runtime_result, usage_entry)
        manager.record_outcome(
            provider_id=provider_id,
            model=model,
            runtime=runtime,
            success=effective_success,
            rework=effective_rework,
            quality_score=effective_quality_score,
            latency_ms=latency_ms,
            evidence_ref=evidence_ref,
        )
        cost = manager.record_cost_observation(
            provider_id=provider_id,
            model=model,
            runtime=runtime,
            estimated_cost_usd=decision.get("estimatedCostUsd"),
            actual_cost_usd=self._runtime_actual_cost_usd(runtime_result, usage_entry),
            provider_usage=self._runtime_provider_usage(runtime_result, usage_entry),
            latency_ms=latency_ms,
            evidence_ref=evidence_ref,
        )
        return {
            "role": role,
            "providerId": provider_id,
            "model": model,
            "runtime": runtime,
            "success": effective_success,
            "rework": effective_rework,
            "qualityScore": effective_quality_score,
            "latencyMs": latency_ms,
            "costObservationId": cost["id"],
            "tokenStatus": cost["tokenStatus"],
            "costStatus": cost["costStatus"],
            "evidenceRef": evidence_ref,
        }

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
        valid_completion = product_owner_status in {
            "backlog_ready",
            "brief_ready",
            "completed",
            "scope_is_clear",
        }
        success = valid_completion or product_owner_status == "needs_input"
        quality_score = 1.0 if valid_completion else 0.8 if product_owner_status == "needs_input" else 0.0
        observation = self._record_resource_decision_learning(
            manager=AIResourceManager(self.connection),
            role="product_owner",
            decision=resource_decision,
            runtime_result=product_owner_result,
            usage_entry=None,
            evidence_ref=evidence_ref,
            success=success,
            rework=False,
            quality_score=quality_score,
        )
        observations = [observation] if observation else []
        return {
            "status": "recorded" if observations else "not_applicable",
            "loopId": loop_id,
            "projectId": project_id,
            "evidenceRef": evidence_ref,
            "observationCount": len(observations),
            "observations": observations,
        }

    def _product_owner_resource_learning_persistence_failed(
        self,
        *,
        project_id: str,
        loop_id: str,
        evidence_ref: str,
        error: Exception,
    ) -> dict[str, Any]:
        return {
            "status": "persistence_failed",
            "loopId": loop_id,
            "projectId": project_id,
            "evidenceRef": evidence_ref,
            "role": "product_owner",
            "reason": str(redact_secrets(str(error))),
            "observationCount": 0,
            "observations": [],
        }

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
        try:
            return self._record_product_owner_resource_learning(
                project_id=project_id,
                loop_id=loop_id,
                resource_decision=resource_decision,
                product_owner_result=product_owner_result,
                product_owner_status=product_owner_status,
                evidence_ref=evidence_ref,
            )
        except Exception as error:
            return self._product_owner_resource_learning_persistence_failed(
                project_id=project_id,
                loop_id=loop_id,
                evidence_ref=evidence_ref,
                error=error,
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
        observations: list[dict[str, Any]] = []
        manager = AIResourceManager(self.connection)
        for role_plan, usage_entry in self._resource_observation_targets(
            team_schedule=team_schedule,
            runtime_result=runtime_result,
        ):
            decision = role_plan.get("resourceDecision") or {}
            observation = self._record_resource_decision_learning(
                manager=manager,
                role=str(role_plan.get("role") or ""),
                decision=decision,
                runtime_result=runtime_result,
                usage_entry=usage_entry,
                evidence_ref=evidence_ref,
                success=success,
                rework=rework,
                quality_score=quality_score,
            )
            if observation:
                observations.append(observation)
        return {
            "status": "recorded" if observations else "not_applicable",
            "loopId": loop_id,
            "projectId": project_id,
            "evidenceRef": evidence_ref,
            "observationCount": len(observations),
            "observations": observations,
        }

    @staticmethod
    def _resource_learning_persistence_failed(
        *,
        project_id: str,
        loop_id: str,
        evidence_ref: str,
        error: Exception,
    ) -> dict[str, Any]:
        return {
            "status": "persistence_failed",
            "loopId": loop_id,
            "projectId": project_id,
            "evidenceRef": evidence_ref,
            "reason": str(redact_secrets(str(error))),
            "observationCount": 0,
            "observations": [],
        }

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
        try:
            return self._record_resource_learning(
                project_id=project_id,
                loop_id=loop_id,
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                evidence_ref=evidence_ref,
                success=success,
                rework=rework,
                quality_score=quality_score,
            )
        except Exception as error:
            return self._resource_learning_persistence_failed(
                project_id=project_id,
                loop_id=loop_id,
                evidence_ref=evidence_ref,
                error=error,
            )

    def _attach_resource_learning_to_result(
        self,
        result: dict[str, Any],
        resource_learning: dict[str, Any],
    ) -> dict[str, Any]:
        loop = result["loop"]
        durable = self._durable_run_context(loop)
        durable["resourceLearning"] = resource_learning
        durable["updatedAt"] = utc_now()
        result["loop"] = self.repository.update_loop_context(
            loop["id"],
            context={**loop["context"], "durableRun": redact_secrets(durable)},
        )
        return result

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
        """Block after runtime execution and preserve ResourceManager learning evidence."""
        blocked_result = self._block_run(
            loop,
            stage=stage,
            reason=reason,
            actor=actor,
            details=details,
            thread_id=thread_id,
        )
        evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
        if not evidence_ref:
            return blocked_result
        resource_learning = self._record_resource_learning_best_effort(
            project_id=project_id,
            loop_id=loop["id"],
            team_schedule=team_schedule,
            runtime_result=runtime_result,
            evidence_ref=evidence_ref,
            success=success,
            rework=rework,
            quality_score=quality_score,
        )
        return self._attach_resource_learning_to_result(blocked_result, resource_learning)

    def _attach_product_owner_resource_learning_to_result(
        self,
        result: dict[str, Any],
        resource_learning: dict[str, Any],
    ) -> dict[str, Any]:
        loop = result["loop"]
        durable = self._durable_run_context(loop)
        durable["productOwner"] = {
            **dict(durable.get("productOwner") or {}),
            "resourceLearning": resource_learning,
        }
        durable["updatedAt"] = utc_now()
        result["loop"] = self.repository.update_loop_context(
            loop["id"],
            context={**loop["context"], "durableRun": redact_secrets(durable)},
        )
        return result

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
        )
        result = self._ensure_thread_and_similarity(run)
        if result is not None:
            return result
        result = self._check_workspace_and_git(run)
        if result is not None:
            return result
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
        while True:
            result = self._execute_developer_phase(run)
            if result is not None:
                return result
            result = self._capture_review_evidence(run)
            if result is not None:
                return result
            # Puente temporal mientras se extraen las fases restantes.
            thread_id = run.thread_id
            loop = run.loop
            git = run.git
            workspace = run.workspace
            runtime_result = run.runtime_result
            runtime_status = run.runtime_status
            review = run.review
            evidence_ids = run.evidence_ids
            agent_tasks = run.agent_tasks
            team_schedule = run.team_schedule

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
            run.loop = loop
            qa_results = (
                runtime_result.get("qaResults") if isinstance(runtime_result.get("qaResults"), list) else []
            )
            qa_verdict = str((runtime_result.get("evidencePackage") or {}).get("qaVerdict") or "").lower()
            qa_statuses = [
                str(result.get("status") or "").strip().lower()
                for result in qa_results
                if isinstance(result, dict)
            ]
            qa_block_details = {
                **runtime_result,
                "status": "qa_blocked",
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "runtimeStatus": runtime_status,
                "qaVerdict": qa_verdict,
                "qaResults": qa_results,
                "review": review,
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            }
            if runtime_status == "qa_failed" or qa_verdict == "failed" or "failed" in qa_statuses:
                reason = str(runtime_result.get("reason") or "QA failed and requires rework.")
                evidence = self._record_run_evidence(
                    project_id=project_id,
                    loop_id=loop["id"],
                    stage="qa",
                    status="reworking",
                    reason=reason,
                    details={**runtime_result, "reworkRound": run.rework_round},
                )
                try:
                    resource_learning = self._record_resource_learning(
                        project_id=project_id,
                        loop_id=loop["id"],
                        team_schedule=team_schedule,
                        runtime_result=runtime_result,
                        evidence_ref=evidence["id"],
                        success=False,
                        rework=True,
                        quality_score=0.0,
                    )
                except Exception as error:
                    learning_reason = f"AI resource learning persistence failed: {redact_secrets(str(error))}"
                    return self._block_run(
                        loop,
                        stage="resource_learning",
                        reason=learning_reason,
                        actor=actor,
                        details={
                            "status": "persistence_failed",
                            "reason": learning_reason,
                            "evidenceRef": evidence["id"],
                            "runtimeStatus": runtime_status,
                            "qaVerdict": qa_verdict,
                            "qaResults": qa_results,
                            "teamSchedule": team_schedule,
                            "agentTaskIds": [task["id"] for task in agent_tasks],
                        },
                        durable_context={
                            "rework": {"source": "qa", "reason": reason, "runtimeStatus": runtime_status},
                            "resourceLearning": {
                                "status": "persistence_failed",
                                "reason": learning_reason,
                                "evidenceRef": evidence["id"],
                            },
                        },
                        thread_id=thread_id,
                    )
                try:
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
                                "rework": {
                                    "source": "qa",
                                    "reason": reason,
                                    "runtimeStatus": runtime_status,
                                    "reworkRound": run.rework_round,
                                },
                                "resourceLearning": resource_learning,
                            },
                            evidence_package_ids=[*evidence_ids, evidence["id"]],
                        ),
                        metadata={"evidencePackageId": evidence["id"]},
                        thread_id=thread_id,
                    )
                except ProductLoopStopConditionError as stop_error:
                    return self._block_run(
                        loop,
                        stage="qa_rework",
                        reason=str(stop_error),
                        actor=actor,
                        details={
                            "status": "stop_condition",
                            "reason": str(stop_error),
                            "runtimeStatus": runtime_status,
                            "qaVerdict": qa_verdict,
                            "reworkRound": run.rework_round,
                            "teamSchedule": team_schedule,
                            "agentTaskIds": [task["id"] for task in agent_tasks],
                        },
                        thread_id=thread_id,
                    )
                run.loop = reworked
                if run.rework_round >= DEFAULT_AUTO_REWORK_ROUNDS:
                    return self._run_result(
                        reworked, status=REWORK_STATE, reason=reason, evidence_package=evidence
                    )
                run.rework_round += 1
                run.rework_feedback = self._qa_rework_feedback(qa_results)
                run.task_id = f"{run.base_task_id}:r{run.rework_round}"
                loop = self._transition_run_state(
                    reworked,
                    to_state="executing",
                    reason=(
                        f"Auto rework round {run.rework_round}: re-executing DeveloperAgent with QA feedback."
                    ),
                    trigger="auto_rework",
                    actor=actor,
                    context_patch=self._durable_run_patch(
                        reworked,
                        {
                            "status": "executing",
                            "autoRework": {"round": run.rework_round, "reason": reason},
                        },
                    ),
                    thread_id=thread_id,
                )
                run.loop = loop
                continue
            break
        if not qa_results:
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="qa",
                reason="QA results are required before Product Loop delivery approval.",
                actor=actor,
                details={
                    **qa_block_details,
                    "reason": "QA results are required before Product Loop delivery approval.",
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )
        non_passing_qa = [
            result
            for result in qa_results
            if not isinstance(result, dict) or str(result.get("status") or "").strip().lower() != "passed"
        ]
        if non_passing_qa:
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="qa",
                reason="QA results must all pass before Product Loop delivery approval.",
                actor=actor,
                details={
                    **qa_block_details,
                    "reason": "QA results must all pass before Product Loop delivery approval.",
                    "nonPassingQaResults": non_passing_qa,
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )
        if qa_verdict in {"blocked", "not_started"}:
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="qa",
                reason=f"QA verdict is {qa_verdict}; Product Loop delivery approval requires passing QA evidence.",
                actor=actor,
                details={
                    **qa_block_details,
                    "reason": (
                        f"QA verdict is {qa_verdict}; Product Loop delivery approval requires "
                        "passing QA evidence."
                    ),
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )
        if runtime_status not in {"completed", "evidence_ready"}:
            reason = str(runtime_result.get("reason") or f"Runtime ended with status {runtime_status}.")
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="runtime",
                reason=reason,
                actor=actor,
                details={
                    **runtime_result,
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "qaResults": qa_results,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
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
        except Exception as error:
            reason = f"gitleaks delivery gate failed to run: {redact_secrets(str(error))}"
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="gitleaks",
                reason=reason,
                actor=actor,
                details={
                    "status": "failed",
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "qaResults": qa_results,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )
        if gitleaks.get("status") != "completed" or bool(gitleaks.get("deliveryBlocked")):
            reason = str(gitleaks.get("reason") or "gitleaks blocked delivery.")
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="gitleaks",
                reason=reason,
                actor=actor,
                details={
                    **gitleaks,
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "qaResults": qa_results,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )

        security_agent = security_runner or SecurityAgentRunner(self.connection, root=run.effective_root)
        patch_artifact_id = str((runtime_result.get("diffSummary") or {}).get("patchArtifactId") or "")
        security_payload: dict[str, Any] = {
            "projectId": project_id,
            "workspaceId": workspace["id"],
            "taskId": f"{run.task_id}.security",
            "diffArtifactId": patch_artifact_id or None,
            "workflowRunId": loop["id"],
            "workflowStepId": "security_review",
        }
        story_specs_prompt = self._story_specs_for_tasks(agent_tasks)
        if story_specs_prompt:
            security_payload["storySpecs"] = story_specs_prompt
        security_resource = self._security_execution_resource(team_schedule)
        if security_resource:
            security_payload["runModelAnalysis"] = True
            security_payload["preferredRuntime"] = security_resource["preferredRuntime"]
            if security_resource.get("model"):
                security_payload["model"] = security_resource["model"]
        try:
            security_result = security_agent.run(security_payload)
        except Exception as error:
            reason = f"SecurityAgent delivery gate failed to run: {redact_secrets(str(error))}"
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="security_agent",
                reason=reason,
                actor=actor,
                details={
                    "status": "failed",
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "runtimeStatus": runtime_status,
                    "qaResults": qa_results,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )
        security_verdict = str(security_result.get("verdict") or "").strip().lower()
        security_summary = {
            "verdict": security_verdict,
            "reason": str(security_result.get("reason") or ""),
            "agentRunId": str((security_result.get("agentRun") or {}).get("id") or ""),
            "evidencePackageId": str((security_result.get("evidencePackage") or {}).get("id") or ""),
            "findingsArtifactId": str((security_result.get("findingsArtifact") or {}).get("id") or ""),
            "findingCount": len(security_result.get("findings") or []),
        }
        if security_verdict == "blocked":
            reason = str(security_result.get("reason") or "SecurityAgent blocked delivery.")
            return self._block_after_runtime_with_resource_learning(
                loop,
                project_id=project_id,
                stage="security_agent",
                reason=reason,
                actor=actor,
                details={
                    **security_summary,
                    "status": "security_blocked",
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "runtimeStatus": runtime_status,
                    "qaResults": qa_results,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                thread_id=thread_id,
            )

        diff_ref = _diff_ref_from_review(review)
        diff_summary = _diff_summary_from_review(review)
        security_evidence = self._record_run_evidence(
            project_id=project_id,
            loop_id=loop["id"],
            stage="gitleaks",
            status="completed",
            reason=str(gitleaks.get("reason") or "gitleaks passed."),
            details={**gitleaks, "securityAgent": security_summary},
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
                {
                    "command": "security_agent",
                    "status": "passed",
                    "metadata": {"workspaceId": workspace["id"], **security_summary},
                },
            ],
            tool_calls=gitleaks.get("toolCalls") or [],
            policy_decisions=[
                {"id": item} for item in (gitleaks.get("policyDecisionIds") or []) if str(item).strip()
            ],
        )
        try:
            resource_learning = self._record_resource_learning(
                project_id=project_id,
                loop_id=loop["id"],
                team_schedule=team_schedule,
                runtime_result=runtime_result,
                evidence_ref=security_evidence["id"],
                success=True,
                rework=False,
                quality_score=1.0,
            )
        except Exception as error:
            reason = f"AI resource learning persistence failed: {redact_secrets(str(error))}"
            return self._block_run(
                loop,
                stage="resource_learning",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "evidenceRef": security_evidence["id"],
                    "review": review,
                    "gitleaks": gitleaks,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                durable_context={
                    "review": review,
                    "gitleaks": gitleaks,
                    "resourceLearning": {
                        "status": "persistence_failed",
                        "reason": reason,
                        "evidenceRef": security_evidence["id"],
                    },
                },
                thread_id=thread_id,
            )
        approval_evidence_ids = [*evidence_ids, security_evidence["id"]]
        approval_payload = {
            "loopId": loop["id"],
            "workspaceId": workspace["id"],
            "evidenceRefs": approval_evidence_ids,
            "diffRefs": [diff_ref],
            "changedFiles": review["changedFiles"],
        }
        try:
            approval_job = self.jobs.create_job(
                project_id=project_id,
                kind="product_loop_delivery_approval",
                status="approval_required",
                payload=approval_payload,
            )["job"]
            approval_action = self.jobs.create_action_request(
                job_id=approval_job["id"],
                project_id=project_id,
                action_type="product_loop.approve_delivery",
                risk_level="medium",
                command="approve product loop delivery",
                payload=approval_payload,
                reason="Review Product Loop diff, QA, and gitleaks evidence before delivery.",
            )
        except Exception as error:
            reason = f"Product Loop delivery approval persistence failed: {redact_secrets(str(error))}"
            return self._block_run(
                loop,
                stage="approval",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "reason": reason,
                    "review": review,
                    "gitleaks": gitleaks,
                    "resourceLearning": resource_learning,
                    **approval_payload,
                },
                durable_context={
                    "review": review,
                    "gitleaks": gitleaks,
                    "resourceLearning": resource_learning,
                    "approval": {
                        "status": "unavailable",
                        "reason": reason,
                        **approval_payload,
                    },
                },
                thread_id=thread_id,
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
                {
                    "status": "review_ready",
                    "gitleaks": gitleaks,
                    "review": review,
                    "approval": approval,
                    "resourceLearning": resource_learning,
                },
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

    def _ensure_thread_and_similarity(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Normaliza el mensaje, persiste thread+loop y aplica la puerta de funcionalidad existente.

        Devuelve el resultado terminal del bloqueo por similitud o ``None`` para continuar.
        """
        message = run.message
        root = run.root
        title = run.title
        run_metadata = run.run_metadata
        project_id = run.project_id
        session_id = run.session_id
        thread_id = run.thread_id
        actor = run.actor
        message_text = str(message or "").strip()
        if not message_text:
            raise ProductLoopTransitionError("Product Loop user message is required.")
        effective_root = Path(root).resolve(strict=False) if root is not None else self.root
        resolved_title = title or message_text.splitlines()[0][:80] or "Product Loop"
        request_meta = self._sanitize_untrusted_resource_approval_metadata(
            strip_untrusted_resource_cost_policy_metadata(redact_secrets(run_metadata or {}))
        )
        plan_only = bool(request_meta.get("planOnly") or request_meta.get("plan_only"))
        thread = self._create_thread(
            project_id=project_id,
            message=message_text,
            title=resolved_title,
            session_id=session_id,
            thread_id=thread_id,
            message_id=request_meta.get("messageId")
            if isinstance(request_meta.get("messageId"), str)
            else None,
            message_metadata=request_meta,
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
                    "planOnly": plan_only,
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
        run.message_text = message_text
        run.effective_root = effective_root
        run.resolved_title = resolved_title
        run.request_meta = request_meta
        run.plan_only = plan_only
        run.thread = thread
        run.thread_id = thread_id
        run.loop = loop
        return None

    def _check_workspace_and_git(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Registra agentes de delivery y valida workspace root, limpieza y remotos de git.

        Devuelve el resultado terminal de un bloqueo de workspace/git o ``None`` para continuar.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread = run.thread
        thread_id = run.thread_id
        request_meta = run.request_meta
        plan_only = run.plan_only
        effective_root = run.effective_root
        git_service = run.git_service
        product_owner_runner = run.product_owner_runner
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
                    "planOnly": plan_only,
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
        try:
            git_state = git.status(project_id)
        except Exception as error:
            reason = f"Git workspace status check failed: {redact_secrets(str(error))}"
            git_state = {
                "status": "failed",
                "reason": reason,
                "projectId": project_id,
                "dirty": False,
                "changedFiles": [],
                "untrackedFiles": [],
                "stagedFiles": [],
            }
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
        configured_remotes = self.connection.execute(
            "SELECT COUNT(*) AS total FROM git_remotes WHERE project_id = ?",
            (project_id,),
        ).fetchone()["total"]
        if configured_remotes and not (git_state.get("remotes") or []):
            reason = (
                "The project has a configured Git remote, but the repository exposes none; "
                "Product Loop delivery to that remote cannot continue."
            )
            return self._block_run(
                loop,
                stage="git",
                reason=reason,
                actor=actor,
                details={**git_state, "remoteMissing": True, "configuredRemotes": configured_remotes},
                thread_id=thread_id,
            )
        run.loop = loop
        run.git = git
        run.git_state = git_state
        run.product_owner = product_owner
        return None

    def _select_product_owner_resources(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Selecciona el recurso IA del ProductOwnerAgent y verifica que su runtime sea ejecutable.

        Devuelve el resultado terminal de un bloqueo de recurso/runtime o ``None`` para continuar.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread_id = run.thread_id
        request_meta = run.request_meta
        preferred_runtime = run.preferred_runtime
        product_owner = run.product_owner
        product_owner_task_id = f"product-owner-{stable_task_suffix(thread_id, loop['id'])}"
        try:
            product_owner_resource_decision, product_owner_resource_blocker = (
                self._product_owner_resource_selection(
                    project_id=project_id,
                    loop_id=loop["id"],
                    task_id=product_owner_task_id,
                    request_meta=request_meta,
                )
            )
        except Exception as error:
            reason = f"AIResourceManager failed to select a ProductOwnerAgent resource: {redact_secrets(str(error))}"
            product_owner_resource_decision = {
                "selected": None,
                "approvalRequired": False,
                "decisionReason": reason,
                "policyResult": {"status": "failed"},
            }
            resource_blocker = {
                "role": "product_owner",
                "taskId": product_owner_task_id,
                "reason": reason,
                "decision": product_owner_resource_decision,
            }
            return self._block_run(
                loop,
                stage="resource_manager",
                reason=reason,
                actor=actor,
                details={
                    "resourceBlockers": [resource_blocker],
                    "agentRole": "product_owner",
                    "agentId": PRODUCT_OWNER_AGENT_ID,
                },
                durable_context={
                    "productOwner": {
                        "status": "resource_blocked",
                        "reason": reason,
                        "resourceDecision": product_owner_resource_decision,
                    }
                },
                thread_id=thread_id,
            )
        if product_owner_resource_blocker:
            return self._block_run(
                loop,
                stage="resource_manager",
                reason=(
                    "AIResourceManager could not select an approved AI resource for ProductOwnerAgent: "
                    f"{product_owner_resource_blocker['reason']}"
                ),
                actor=actor,
                details={
                    "resourceBlockers": [product_owner_resource_blocker],
                    "agentRole": "product_owner",
                    "agentId": PRODUCT_OWNER_AGENT_ID,
                },
                durable_context={
                    "productOwner": {
                        "status": "resource_blocked",
                        "resourceDecision": product_owner_resource_decision,
                    }
                },
                thread_id=thread_id,
            )
        product_owner_selected_resource = product_owner_resource_decision.get("selected") or {}
        product_owner_preferred_runtime = self._product_owner_runtime_id_for_resource_selection(
            product_owner_selected_resource
        )
        product_owner_effective_runtime = product_owner_preferred_runtime or preferred_runtime

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
            try:
                product_owner_readiness = product_owner.status(
                    preferred_runtime=product_owner_effective_runtime
                )
            except Exception as error:
                reason = f"ProductOwnerAgent runtime readiness check failed: {redact_secrets(str(error))}"
                product_owner_readiness = {
                    "executable": False,
                    "status": "failed",
                    "selectedRuntimeId": product_owner_effective_runtime,
                    "reason": reason,
                }
        else:
            product_owner_readiness = {
                "executable": True,
                "selectedRuntimeId": product_owner_effective_runtime or "injected_product_owner_runner",
                "reason": "Injected ProductOwnerAgent runner has no readiness hook.",
            }
        self._record_thread_event(
            thread_id=thread_id,
            event_type="runtime_selected",
            agent_role="product_owner",
            payload={
                "loopId": loop["id"],
                "runtimeId": product_owner_readiness.get("selectedRuntimeId")
                or product_owner_effective_runtime,
                "executable": bool(product_owner_readiness.get("executable")),
                "reason": product_owner_readiness.get("reason"),
                "resourceSelection": product_owner_resource_decision,
            },
        )
        if not bool(product_owner_readiness.get("executable")):
            reason = str(
                product_owner_readiness.get("reason")
                or "No executable ProductOwnerAgent runtime is configured."
            )
            product_owner_context = {
                "status": "runtime_unavailable",
                "reason": reason,
                "resourceDecision": product_owner_resource_decision,
                "runtimeReadiness": product_owner_readiness,
            }
            return self._block_run(
                loop,
                stage="product_owner_runtime",
                reason=reason,
                actor=actor,
                details={
                    **product_owner_readiness,
                    "resourceDecision": product_owner_resource_decision,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
        run.loop = loop
        run.product_owner_task_id = product_owner_task_id
        run.product_owner_resource_decision = product_owner_resource_decision
        run.product_owner_selected_resource = product_owner_selected_resource
        run.product_owner_preferred_runtime = product_owner_preferred_runtime
        return None

    def _run_project_assessment(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Corre el assessment del proyecto existente antes del discovery.

        Devuelve el resultado terminal si el assessment falla o no completa, o ``None`` para continuar.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread_id = run.thread_id
        effective_root = run.effective_root
        assessment_runner = run.assessment_runner
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
        run.assessment_result = assessment_result
        return None

    def _run_discovery_phase(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Transiciona a discovery, asigna el workspace del PO y ejecuta ProductOwnerAgent.

        Devuelve el resultado terminal de un bloqueo de workspace/ejecución o ``None`` para continuar.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread = run.thread
        thread_id = run.thread_id
        message_text = run.message_text
        effective_root = run.effective_root
        preferred_runtime = run.preferred_runtime
        assessment_result = run.assessment_result
        product_owner = run.product_owner
        product_owner_task_id = run.product_owner_task_id
        product_owner_resource_decision = run.product_owner_resource_decision
        product_owner_selected_resource = run.product_owner_selected_resource
        product_owner_preferred_runtime = run.product_owner_preferred_runtime
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

        try:
            product_owner_workspace = WorkspacesRepository(
                self.connection, root=effective_root
            ).allocate_workspace(
                project_id=project_id,
                task_id=product_owner_task_id,
                agent_id=PRODUCT_OWNER_AGENT_ID,
                reason="ProductLoopCoordinator ProductOwnerAgent workspace",
                branch_name=(
                    f"{self._work_branch_prefix(project_id)}/product-owner-"
                    f"{stable_task_suffix(thread_id, loop['id'])}"
                ),
                base_branch=self._resolve_project_base_branch(project_id),
                reuse_existing=True,
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

        goal_statement = str(
            resolve_setting_value(
                connection=self.connection,
                key="project.goal.statement",
                project_id=project_id,
            )
            or ""
        ).strip()
        product_owner_payload = {
            "projectId": project_id,
            "workspaceId": product_owner_workspace["id"],
            "taskId": product_owner_task_id,
            "idea": message_text,
            "preferredRuntime": product_owner_preferred_runtime or preferred_runtime,
            "model": product_owner_selected_resource.get("model"),
            # Solo lo que el agente convierte en señales de prompt: el resultado completo del
            # assessment (job/agentRun/artifact/decision) ya queda auditado en su propio slice.
            "assessment": redact_secrets(
                {
                    key: (assessment_result or {}).get(key)
                    for key in ("status", "assessment", "findings")
                    if (assessment_result or {}).get(key) is not None
                }
            ),
            "workflowContext": {
                "workflowRunId": loop["id"],
                "workflowStepId": "product_owner",
            },
            "metadata": {
                "loopId": loop["id"],
                "projectThreadId": thread.get("id"),
                "source": "product_loop_coordinator",
                "resourceSelection": product_owner_resource_decision,
            },
        }
        if goal_statement:
            product_owner_payload["goalStatement"] = goal_statement
        # Sin la iniciativa del hilo el agente no ve el brief ni las preguntas ya formuladas, y
        # vuelve a preguntar lo mismo en cada turno.
        thread_initiative = (
            self.discovery.find_initiative_by_thread(project_id, thread_id) if thread_id else None
        )
        if thread_initiative is not None:
            product_owner_payload["initiativeId"] = thread_initiative["id"]
        try:
            product_owner_result = product_owner.run(product_owner_payload)
        except Exception as error:
            reason = str(redact_secrets(str(error)))
            product_owner_context = {
                "status": "runtime_failed",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
                "resourceDecision": product_owner_resource_decision,
            }
            blocked_result = self._block_run(
                loop,
                stage="product_owner",
                reason=reason,
                actor=actor,
                details={
                    "status": "runtime_failed",
                    "reason": reason,
                    "workspaceId": product_owner_workspace["id"],
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
            evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
            if evidence_ref:
                resource_learning = self._record_product_owner_resource_learning_best_effort(
                    project_id=project_id,
                    loop_id=loop["id"],
                    resource_decision=product_owner_resource_decision,
                    product_owner_result={"status": "runtime_failed", "reason": reason},
                    product_owner_status="runtime_failed",
                    evidence_ref=evidence_ref,
                )
                blocked_result = self._attach_product_owner_resource_learning_to_result(
                    blocked_result,
                    resource_learning,
                )
            return blocked_result
        run.loop = loop
        run.product_owner_workspace = product_owner_workspace
        run.product_owner_result = product_owner_result
        return None

    def _persist_product_owner_results(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Valida y persiste la salida del PO: artefactos, brief, preguntas, decisiones y evidencia.

        Construye ``product_owner_context`` y los ids de evidencia del PO; devuelve el resultado
        terminal de un bloqueo de validación/persistencia o ``None`` para continuar.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread = run.thread
        thread_id = run.thread_id
        message_text = run.message_text
        resolved_title = run.resolved_title
        effective_root = run.effective_root
        product_owner_result = run.product_owner_result
        product_owner_workspace = run.product_owner_workspace
        product_owner_resource_decision = run.product_owner_resource_decision
        try:
            output = self._product_owner_output(product_owner_result)
            product_owner_status = self._product_owner_flow_status(product_owner_result, output=output)
        except ProductOwnerOutputValidationError as error:
            raw_output = (
                product_owner_result.get("output") if isinstance(product_owner_result, dict) else None
            )
            details = {
                "status": "failed_validation",
                "outputStatus": product_owner_result.get("status")
                if isinstance(product_owner_result, dict)
                else None,
                "reason": product_owner_result.get("reason")
                if isinstance(product_owner_result, dict)
                else None,
                "outputType": type(raw_output).__name__,
            }
            product_owner_context = {
                "status": "failed_validation",
                "reason": str(redact_secrets(str(error))),
                "workspaceId": product_owner_workspace["id"],
                "resourceDecision": product_owner_resource_decision,
            }
            blocked_result = self._block_run(
                loop,
                stage="product_owner",
                reason=str(redact_secrets(str(error))),
                actor=actor,
                details=details,
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
            evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
            if evidence_ref and isinstance(product_owner_result, dict):
                resource_learning = self._record_product_owner_resource_learning_best_effort(
                    project_id=project_id,
                    loop_id=loop["id"],
                    resource_decision=product_owner_resource_decision,
                    product_owner_result=product_owner_result,
                    product_owner_status="failed_validation",
                    evidence_ref=evidence_ref,
                )
                blocked_result = self._attach_product_owner_resource_learning_to_result(
                    blocked_result,
                    resource_learning,
                )
            return blocked_result
        try:
            product_owner_artifact = self._write_json_artifact(
                root=effective_root,
                project_id=project_id,
                name="product_owner_output.json",
                kind="product_owner_output",
                payload={"status": product_owner_status, "result": product_owner_result, "output": output},
            )
        except Exception as error:
            reason = f"ProductOwnerAgent output artifact persistence failed: {redact_secrets(str(error))}"
            product_owner_context = {
                "status": "persistence_failed",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
                "resourceDecision": product_owner_resource_decision,
            }
            return self._block_run(
                loop,
                stage="product_owner",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "workspaceId": product_owner_workspace["id"],
                    "reason": reason,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
        try:
            initiative = self._ensure_product_initiative(
                project_id=project_id,
                message=message_text,
                title=resolved_title,
                result=product_owner_result,
                output=output,
                thread_id=thread_id,
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
        except Exception as error:
            reason = f"ProductOwnerAgent brief persistence failed: {redact_secrets(str(error))}"
            product_owner_context = {
                "status": "persistence_failed",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
                "artifactIds": [product_owner_artifact["id"]],
                "resourceDecision": product_owner_resource_decision,
            }
            return self._block_run(
                loop,
                stage="product_owner",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "workspaceId": product_owner_workspace["id"],
                    "artifactIds": [product_owner_artifact["id"]],
                    "reason": reason,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
        try:
            product_owner_output_record = self._persist_product_owner_output_record(
                project_id=project_id,
                initiative_id=initiative["id"],
                brief_id=brief["id"],
                output=output,
                result=product_owner_result,
                artifact_id=product_owner_artifact["id"],
            )
        except Exception as error:
            reason = f"ProductOwnerAgent output persistence failed: {redact_secrets(str(error))}"
            product_owner_context = {
                "status": "persistence_failed",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
                "briefId": brief["id"],
                "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                "resourceDecision": product_owner_resource_decision,
            }
            return self._block_run(
                loop,
                stage="product_owner",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "workspaceId": product_owner_workspace["id"],
                    "briefId": brief["id"],
                    "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                    "reason": reason,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
        try:
            clarification_questions = self._persist_clarification_questions(
                project_id=project_id,
                initiative_id=initiative["id"],
                output=output,
                thread_id=thread_id,
                source_message_id=thread["messageId"],
                result=product_owner_result,
            )
            product_decisions = self._persist_product_decisions(
                project_id=project_id,
                initiative_id=initiative["id"],
                brief_id=brief["id"],
                output=output,
                thread_id=thread_id,
                source_message_id=thread["messageId"],
                result=product_owner_result,
            )
            pending_thread_decisions = self._pending_product_owner_thread_decisions(thread_id)
        except Exception as error:
            reason = f"ProductOwnerAgent question/decision persistence failed: {redact_secrets(str(error))}"
            product_owner_context = {
                "status": "persistence_failed",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                "resourceDecision": product_owner_resource_decision,
            }
            return self._block_run(
                loop,
                stage="product_owner",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "workspaceId": product_owner_workspace["id"],
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "briefId": brief["id"],
                    "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                    "reason": reason,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
        if product_owner_status == "needs_input" and not pending_thread_decisions:
            reason = (
                "ProductOwnerAgent returned needs_input but did not create a pending actionable "
                "thread decision with options."
            )
            product_owner_context = {
                "status": "failed_validation",
                "reason": reason,
                "workspaceId": product_owner_workspace["id"],
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                "resourceDecision": product_owner_resource_decision,
                "clarificationQuestionIds": [item["id"] for item in clarification_questions],
                "productDecisionIds": [item["id"] for item in product_decisions],
                "pendingThreadDecisions": [],
            }
            blocked_result = self._block_run(
                loop,
                stage="product_owner",
                reason=reason,
                actor=actor,
                details={
                    "status": "failed_validation",
                    "outputStatus": product_owner_status,
                    "workspaceId": product_owner_workspace["id"],
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "briefId": brief["id"],
                    "artifactIds": [product_owner_artifact["id"], brief_artifact["id"]],
                    "clarificationQuestionIds": [item["id"] for item in clarification_questions],
                    "productDecisionIds": [item["id"] for item in product_decisions],
                    "pendingThreadDecisions": [],
                    "reason": reason,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
            evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
            if evidence_ref:
                resource_learning = self._record_product_owner_resource_learning_best_effort(
                    project_id=project_id,
                    loop_id=loop["id"],
                    resource_decision=product_owner_resource_decision,
                    product_owner_result=product_owner_result,
                    product_owner_status="failed_validation",
                    evidence_ref=evidence_ref,
                )
                blocked_result = self._attach_product_owner_resource_learning_to_result(
                    blocked_result,
                    resource_learning,
                )
            return blocked_result
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
        try:
            product_owner_resource_learning = self._record_product_owner_resource_learning(
                project_id=project_id,
                loop_id=loop["id"],
                resource_decision=product_owner_resource_decision,
                product_owner_result=product_owner_result,
                product_owner_status=product_owner_status,
                evidence_ref=po_evidence["id"],
            )
        except Exception as error:
            reason = f"ProductOwnerAgent resource learning persistence failed: {redact_secrets(str(error))}"
            resource_learning = {
                "status": "persistence_failed",
                "reason": reason,
                "evidenceRef": po_evidence["id"],
                "role": "product_owner",
            }
            product_owner_context = {
                "status": product_owner_status,
                "reason": product_owner_result.get("reason") or output.get("recommendedNextAction") or "",
                "workspaceId": product_owner_workspace["id"],
                "productOwnerOutputId": product_owner_output_record["id"],
                "briefId": brief["id"],
                "artifactIds": po_artifact_ids,
                "evidencePackageId": po_evidence["id"],
                "resourceDecision": product_owner_resource_decision,
                "resourceLearning": resource_learning,
                "clarificationQuestionIds": [item["id"] for item in clarification_questions],
                "productDecisionIds": [item["id"] for item in product_decisions],
                "pendingThreadDecisions": pending_thread_decisions,
            }
            return self._block_run(
                loop,
                stage="resource_learning",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "reason": reason,
                    "role": "product_owner",
                    "workspaceId": product_owner_workspace["id"],
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "briefId": brief["id"],
                    "artifactIds": po_artifact_ids,
                    "evidenceRef": po_evidence["id"],
                    "resourceDecision": product_owner_resource_decision,
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "resourceLearning": resource_learning,
                },
                thread_id=thread_id,
            )
        product_owner_context = {
            "status": product_owner_status,
            "reason": product_owner_result.get("reason") or output.get("recommendedNextAction") or "",
            "workspaceId": product_owner_workspace["id"],
            "productOwnerOutputId": product_owner_output_record["id"],
            "briefId": brief["id"],
            "artifactIds": po_artifact_ids,
            "evidencePackageId": po_evidence["id"],
            "resourceDecision": product_owner_resource_decision,
            "resourceLearning": product_owner_resource_learning,
            "clarificationQuestionIds": [item["id"] for item in clarification_questions],
            "productDecisionIds": [item["id"] for item in product_decisions],
            "pendingThreadDecisions": pending_thread_decisions,
        }
        evidence_ids = [*self._external_evidence_ids(product_owner_result), po_evidence["id"]]
        self._record_thread_event(
            thread_id=thread_id,
            event_type="product_owner_completed",
            agent_role="product_owner",
            payload={"loopId": loop["id"], **product_owner_context},
        )
        run.output = output
        run.product_owner_status = product_owner_status
        run.brief = brief
        run.product_owner_output_record = product_owner_output_record
        run.pending_thread_decisions = pending_thread_decisions
        run.po_artifact_ids = po_artifact_ids
        run.po_evidence = po_evidence
        run.product_owner_context = product_owner_context
        run.evidence_ids = evidence_ids
        return None

    def _route_product_owner_outcome(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Enruta el estado del PO: needs_input, blocked, research requerida o brief_ready.

        Cada rama devuelve su resultado terminal; ``None`` significa backlog_ready y el
        pipeline sigue hacia la planificación del equipo.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread = run.thread
        thread_id = run.thread_id
        message_text = run.message_text
        request_meta = run.request_meta
        output = run.output
        product_owner_result = run.product_owner_result
        product_owner_status = run.product_owner_status
        product_owner_workspace = run.product_owner_workspace
        product_owner_context = run.product_owner_context
        pending_thread_decisions = run.pending_thread_decisions
        po_artifact_ids = run.po_artifact_ids
        po_evidence = run.po_evidence
        evidence_ids = run.evidence_ids
        brief = run.brief
        if product_owner_status == "needs_input":
            self._set_thread_status_best_effort(
                thread_id=thread_id,
                status="waiting_decision",
                reason="ProductOwnerAgent requires product clarification before development.",
            )
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
            if thread_id and pending_thread_decisions:
                self._create_blocker_remediations_best_effort(
                    project_id=awaiting["projectId"],
                    thread_id=thread_id,
                    loop_id=awaiting["id"],
                    stage="product_owner",
                    reason=product_owner_context["reason"]
                    or "ProductOwnerAgent requires product question input before development.",
                    details={
                        **product_owner_context,
                        "status": "needs_input",
                        "pendingDecisions": pending_thread_decisions,
                    },
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
                reason=str(
                    product_owner_result.get("reason") or "ProductOwnerAgent did not produce a usable output."
                ),
                actor=actor,
                details=product_owner_result,
                durable_context={"productOwner": product_owner_context},
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
                    "ResearchAgent evidence is required before accepting high-impact technical decisions."
                ),
                actor=actor,
                details={
                    "jobId": research_job["id"],
                    "researchStatus": "research_required",
                    "decisions": research_required_decisions,
                    "researchPolicy": self._research_policy(request_meta),
                },
                durable_context={"productOwner": product_owner_context},
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
        return None

    def _plan_team_and_resources(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Arma el plan del equipo desde el backlog: agent_tasks, team schedule, recursos IA y assignments.

        Transiciona a planning/backlog_ready y cierra los runs plan-only; devuelve el resultado
        terminal de un bloqueo o del cierre plan-only, o ``None`` para continuar a la ejecución.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread_id = run.thread_id
        message_text = run.message_text
        request_meta = run.request_meta
        plan_only = run.plan_only
        effective_root = run.effective_root
        git_state = run.git_state
        assessment_result = run.assessment_result
        technical_lead_runner = run.technical_lead_runner
        output = run.output
        product_owner_result = run.product_owner_result
        product_owner_context = run.product_owner_context
        product_owner_output_record = run.product_owner_output_record
        product_owner_workspace = run.product_owner_workspace
        brief = run.brief
        po_artifact_ids = run.po_artifact_ids
        po_evidence = run.po_evidence
        evidence_ids = run.evidence_ids
        try:
            backlog = self._persist_product_owner_backlog(
                project_id=project_id,
                output=output,
                result=product_owner_result,
                product_owner_output_id=product_owner_output_record["id"],
            )
        except Exception as error:
            reason = f"ProductOwnerAgent backlog persistence failed: {redact_secrets(str(error))}"
            return self._block_run(
                loop,
                stage="backlog",
                reason=reason,
                actor=actor,
                details={
                    "status": "persistence_failed",
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "briefId": brief["id"],
                    "workspaceId": product_owner_workspace["id"],
                    "reason": reason,
                },
                durable_context={"productOwner": product_owner_context},
                thread_id=thread_id,
            )
        if not backlog:
            return self._block_run(
                loop,
                stage="backlog",
                reason="ProductOwnerAgent returned backlog_ready without epics/user_stories/acceptance_criteria.",
                actor=actor,
                details={"productOwnerOutputId": product_owner_output_record["id"]},
                durable_context={"productOwner": product_owner_context},
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
        try:
            preliminary_team_schedule = self._team_schedule(
                message=message_text,
                request_meta=request_meta,
                output=output,
                assessment_result=assessment_result,
                git_state=git_state,
            )
        except Exception as error:
            reason = (
                f"TeamScheduler failed to create the preliminary role schedule: {redact_secrets(str(error))}"
            )
            failed_team_schedule = self._failed_team_schedule(phase="preliminary", reason=reason)
            return self._block_run(
                loop,
                stage="team_scheduler",
                reason=reason,
                actor=actor,
                details={
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "backlogArtifactId": backlog_artifact["id"],
                    "teamSchedule": failed_team_schedule,
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": [],
                    "teamSchedule": failed_team_schedule,
                },
                thread_id=thread_id,
            )
        try:
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
        except Exception as error:
            reason = f"TechnicalLeadPlanner failed to generate agent_tasks: {redact_secrets(str(error))}"
            return self._block_run(
                loop,
                stage="technical_lead",
                reason=reason,
                actor=actor,
                details={
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "backlogArtifactId": backlog_artifact["id"],
                    "teamSchedule": preliminary_team_schedule,
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": [],
                    "teamSchedule": preliminary_team_schedule,
                },
                thread_id=thread_id,
            )
        if not agent_tasks:
            return self._block_run(
                loop,
                stage="technical_lead",
                reason="TechnicalLead did not generate agent_tasks; DeveloperAgent execution is not allowed.",
                actor=actor,
                details={
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "backlogArtifactId": backlog_artifact["id"],
                    "teamSchedule": preliminary_team_schedule,
                    "agentTaskIds": [],
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": [],
                    "teamSchedule": preliminary_team_schedule,
                },
                thread_id=thread_id,
            )
        try:
            team_schedule = self._team_schedule(
                message=message_text,
                request_meta=request_meta,
                output=output,
                assessment_result=assessment_result,
                git_state=git_state,
                agent_tasks=agent_tasks,
            )
        except Exception as error:
            reason = f"TeamScheduler failed to align TechnicalLead agent_tasks: {redact_secrets(str(error))}"
            failed_team_schedule = self._failed_team_schedule(
                phase="final",
                reason=reason,
                previous_schedule=preliminary_team_schedule,
            )
            return self._block_run(
                loop,
                stage="team_scheduler",
                reason=reason,
                actor=actor,
                details={
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "backlogArtifactId": backlog_artifact["id"],
                    "teamSchedule": failed_team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": failed_team_schedule,
                },
                thread_id=thread_id,
            )
        unscheduled_roles = self._unscheduled_agent_task_roles(
            agent_tasks=agent_tasks,
            team_schedule=team_schedule,
        )
        if unscheduled_roles:
            return self._block_run(
                loop,
                stage="technical_lead",
                reason=(
                    "TechnicalLead generated agent task roles not covered by TeamScheduler: "
                    f"{', '.join(unscheduled_roles)}. DeveloperAgent execution is not allowed until "
                    "TeamScheduler covers every task role."
                ),
                actor=actor,
                details={
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "unscheduledRoles": unscheduled_roles,
                    "scheduledRoles": [role["role"] for role in team_schedule.get("roles") or []],
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                },
                thread_id=thread_id,
            )
        try:
            team_schedule, resource_blockers = self._team_schedule_with_resource_decisions(
                project_id=project_id,
                loop_id=loop["id"],
                request_meta=request_meta,
                team_schedule=team_schedule,
                agent_tasks=agent_tasks,
            )
        except Exception as error:
            reason = f"AIResourceManager failed to select AI resources: {redact_secrets(str(error))}"
            return self._block_run(
                loop,
                stage="resource_manager",
                reason=reason,
                actor=actor,
                details={
                    "resourceBlockers": [{"role": "team_scheduler", "reason": reason}],
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                },
                thread_id=thread_id,
            )
        if resource_blockers:
            blocked_role = resource_blockers[0]["role"]
            blocked_reason = resource_blockers[0]["reason"]
            return self._block_run(
                loop,
                stage="resource_manager",
                reason=f"AIResourceManager could not select an approved AI resource for role {blocked_role}: {blocked_reason}",
                actor=actor,
                details={
                    "resourceBlockers": resource_blockers,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                },
                thread_id=thread_id,
            )
        try:
            team_assignments = self._create_team_assignments(
                project_id=project_id,
                loop_id=loop["id"],
                agent_tasks=agent_tasks,
                team_schedule=team_schedule,
            )
        except Exception as error:
            reason = f"TeamScheduler failed to persist agent assignments: {redact_secrets(str(error))}"
            return self._block_run(
                loop,
                stage="team_scheduler",
                reason=reason,
                actor=actor,
                details={
                    "status": "assignment_persistence_failed",
                    "productOwnerOutputId": product_owner_output_record["id"],
                    "backlogArtifactId": backlog_artifact["id"],
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                    "reason": reason,
                },
                durable_context={
                    "productOwner": product_owner_context,
                    "backlog": {"artifactId": backlog_artifact["id"]},
                    "agentTasks": agent_tasks,
                    "teamSchedule": team_schedule,
                    "agentAssignments": [],
                },
                thread_id=thread_id,
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
        if plan_only:
            return self._complete_plan_only(
                loop,
                actor=actor,
                thread_id=thread_id,
                product_owner_output_id=product_owner_output_record["id"],
                backlog_artifact_id=backlog_artifact["id"],
                agent_tasks=agent_tasks,
                team_schedule=team_schedule,
                team_assignments=team_assignments,
                evidence_ids=evidence_ids,
            )
        run.loop = loop
        run.backlog_artifact = backlog_artifact
        run.agent_tasks = agent_tasks
        run.team_schedule = team_schedule
        run.team_assignments = team_assignments
        return None

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
        """Resuelve runtime y recurso del DeveloperAgent, asigna workspace y transiciona a executing.

        Devuelve el resultado terminal de un bloqueo de recurso/runtime/workspace o ``None``
        para continuar.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread_id = run.thread_id
        effective_root = run.effective_root
        preferred_runtime = run.preferred_runtime
        runtime_runner = run.runtime_runner
        agent_tasks = run.agent_tasks
        team_schedule = run.team_schedule
        runtime = runtime_runner or DeveloperAgentRunner(self.connection, root=effective_root)
        execution_resource = self._developer_execution_resource(team_schedule)
        mapping_blockers = (
            self._developer_execution_resource_mapping_blockers(team_schedule)
            if not execution_resource
            else []
        )
        if mapping_blockers:
            return self._block_run(
                loop,
                stage="resource_manager",
                reason=(
                    "AIResourceManager selected resources, but none of the execution roles maps to a "
                    "DeveloperAgent runtime."
                ),
                actor=actor,
                details={
                    "resourceBlockers": mapping_blockers,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
        resource_preferred_runtime = str(execution_resource.get("preferredRuntime") or "").strip() or None
        effective_preferred_runtime = resource_preferred_runtime or preferred_runtime
        try:
            readiness = runtime.status(preferred_runtime=effective_preferred_runtime)
        except Exception as error:
            reason = f"DeveloperAgent runtime readiness check failed: {redact_secrets(str(error))}"
            readiness = {
                "executable": False,
                "status": "failed",
                "selectedRuntimeId": effective_preferred_runtime,
                "reason": reason,
                "resourceSelection": execution_resource,
            }
        self._record_thread_event(
            thread_id=thread_id,
            event_type="runtime_selected",
            agent_role="developer",
            payload={
                "loopId": loop["id"],
                "runtimeId": readiness.get("selectedRuntimeId") or effective_preferred_runtime,
                "executable": bool(readiness.get("executable")),
                "reason": readiness.get("reason"),
                "resourceSelection": execution_resource,
            },
        )
        if not bool(readiness.get("executable")):
            reason = str(readiness.get("reason") or "No executable DeveloperAgent runtime is configured.")
            return self._block_run(
                loop, stage="runtime", reason=reason, actor=actor, details=readiness, thread_id=thread_id
            )

        task_id = f"product-loop-{stable_task_suffix(thread_id, loop['id'])}"
        try:
            workspace = WorkspacesRepository(self.connection, root=effective_root).allocate_workspace(
                project_id=project_id,
                task_id=task_id,
                agent_id=DEVELOPER_AGENT_ID,
                reason="ProductLoopCoordinator durable execution workspace",
                branch_name=(
                    f"{self._work_branch_prefix(project_id)}/product-loop-"
                    f"{stable_task_suffix(thread_id, loop['id'])}"
                ),
                base_branch=self._resolve_project_base_branch(project_id),
                reuse_existing=True,
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
                "runtimeId": readiness.get("selectedRuntimeId") or effective_preferred_runtime,
                "workspaceId": workspace["id"],
                "assignmentId": task_id,
                "resourceSelection": execution_resource,
            },
        )
        run.loop = loop
        run.runtime = runtime
        run.execution_resource = execution_resource
        run.effective_preferred_runtime = effective_preferred_runtime
        run.readiness = readiness
        run.task_id = task_id
        run.workspace = workspace
        return None

    def _failover_replacement(
        self,
        *,
        run: _UserMessageRun,
        payload: dict[str, Any],
        attempts: list[dict[str, Any]],
        provider_id: str,
        failed_model: str,
    ) -> dict[str, Any] | None:
        """Elige otro proveedor para reintentar, o ``None`` si ninguno es viable ni asequible.

        El ``task_id`` lleva sufijo por intento porque cada selección inserta una fila inmutable de
        routing: sin el sufijo, el snapshot de costo del hilo sumaría runtimes que nunca corrieron.

        No relaja jamás la política de transporte del rol ni el tope de costo: el failover solo se
        mueve dentro de lo que la política ya permitía.
        """
        excluded = [
            exclusion_for(
                FailureClass(str(item["failureClass"])),
                provider_id=str(item["providerId"]),
                model=str(item["model"]),
            )
            for item in attempts
        ]
        policy = self._resource_role_policy("developer")
        # Instancia nueva: AIResourceManager memoiza el estado de runtimes por proyecto y no lo
        # invalida, asi que reusar la anterior devolveria el mismo proveedor ya caido.
        manager = AIResourceManager(self.connection)
        try:
            decision = manager.select_resource(
                AIResourceRequest(
                    project_id=run.project_id,
                    workflow_run_id=run.loop["id"],
                    agent_id="developer",
                    task_id=f"{run.task_id}:f{len(attempts)}",
                    task_type="developer.implement",
                    risk_level=str((run.team_schedule or {}).get("risk") or "medium"),
                    routing_policy=str((run.team_schedule or {}).get("mode") or "balanced"),
                    required_capabilities=["chat"],
                    preferred_provider_ids=policy["preferredProviderIds"],
                    blocked_resources=policy["blockedResources"],
                    excluded_resources=excluded,
                    context_token_limit=policy["maxTokensPerRun"],
                    role_policy_id=policy["rolePolicyId"],
                    allow_remote=policy["allowRemote"],
                    allow_local=policy["allowLocal"],
                    allow_cli=policy["allowCli"],
                    allow_api=policy["allowApi"],
                    free_tier_only=policy["freeTierOnly"],
                    allow_unknown_cost=policy["allowUnknownCost"],
                    require_approval_for_unknown_cost=policy["requireApprovalForUnknownCost"],
                    require_approval_over_usd=policy["requiresApprovalOverUsd"],
                ),
                record=True,
            )
        except Exception:
            return None
        selected = decision.get("selected") if isinstance(decision, dict) else None
        if not selected:
            return None
        affordable, cost_reason = is_affordable_candidate(
            decision, requires_approval_over_usd=policy["requiresApprovalOverUsd"]
        )
        if not affordable:
            attempts[-1]["costDecision"] = f"runtime_failover_cost_capped: {cost_reason}"
            return None
        next_runtime_id = str(selected.get("providerId") or "").strip()
        next_model = str(selected.get("model") or "").strip()
        # Se compara el par y no solo el proveedor: una falla de transporte excluye un modelo
        # puntual, asi que otro modelo del mismo endpoint sigue siendo un reintento legitimo.
        if not next_runtime_id or (next_runtime_id, next_model) == (provider_id, failed_model):
            return None
        next_payload = dict(payload)
        next_payload["preferredRuntime"] = next_runtime_id
        if selected.get("model"):
            next_payload["model"] = selected["model"]
        else:
            next_payload.pop("model", None)
        next_payload["resourceSelection"] = self._public_resource_decision(decision)
        return next_payload

    def _run_with_failover(
        self,
        *,
        runtime: Any,
        payload: dict[str, Any],
        run: _UserMessageRun,
        attempts: list[dict[str, Any]],
        thread_id: str | None,
    ) -> dict[str, Any]:
        """Ejecuta el runtime y, ante una falla de transporte o cuota, reintenta en otro proveedor.

        Solo reintenta lo que dice algo del proveedor y no del trabajo: un fallo de contrato se
        propaga tal cual, porque repetirlo en otro modelo gasta dinero para obtener el mismo error.
        El reemplazo debe además caber en el tope de costo del rol; si no cabe, se propaga la falla
        y el operador decide.

        Cada intento queda registrado en ``attempts`` y anunciado como evento del hilo: un cambio
        de proveedor silencioso que además puede gastar es peor que un loop lento.

        Raises:
            Exception: la última falla, cuando no corresponde failover o no queda candidato viable.
        """
        current_payload = payload
        for attempt in range(MAX_FAILOVER_ATTEMPTS + 1):
            try:
                return runtime.run(current_payload)
            except Exception as error:
                failure = classify_runtime_failure(error)
                failed_provider = str(current_payload.get("preferredRuntime") or "")
                failed_model = str(current_payload.get("model") or "")
                attempts.append(
                    {
                        "attempt": attempt,
                        "providerId": failed_provider,
                        "model": failed_model,
                        "failureClass": failure.value,
                        "reason": str(redact_secrets(str(error))),
                    }
                )
                if not should_failover(failure) or attempt == MAX_FAILOVER_ATTEMPTS:
                    raise
                replacement = self._failover_replacement(
                    run=run,
                    payload=current_payload,
                    attempts=attempts,
                    provider_id=failed_provider,
                    failed_model=failed_model,
                )
                if replacement is None:
                    raise
                current_payload = replacement
                self._record_thread_event(
                    thread_id=thread_id,
                    event_type="runtime_failover",
                    agent_role="developer",
                    payload={
                        "loopId": run.loop["id"],
                        "runtimeId": current_payload.get("preferredRuntime"),
                        "previousRuntimeId": failed_provider,
                        "failureClass": failure.value,
                        "attempt": attempt,
                        "reason": attempts[-1]["reason"],
                    },
                )
        raise RuntimeError("runtime_failover_exhausted")

    def _execute_developer_phase(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Construye el payload del DeveloperAgent y lo ejecuta en el workspace aislado.

        Devuelve el resultado terminal (con learning de recursos) si el runtime falla, o
        ``None`` para continuar con la captura de evidencia.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread = run.thread
        thread_id = run.thread_id
        message_text = run.message_text
        qa_commands = run.qa_commands
        runtime = run.runtime
        readiness = run.readiness
        task_id = run.task_id
        workspace = run.workspace
        execution_resource = run.execution_resource
        effective_preferred_runtime = run.effective_preferred_runtime
        agent_tasks = run.agent_tasks
        team_schedule = run.team_schedule
        team_assignments = run.team_assignments
        product_owner_output_record = run.product_owner_output_record
        backlog_artifact = run.backlog_artifact
        instruction = _bounded_instruction(message_text)
        if run.rework_feedback:
            instruction = (
                f"{instruction}\n\n[QA rework feedback - round {run.rework_round}]\n{run.rework_feedback}"
            )
        developer_payload = {
            "projectId": project_id,
            "workspaceId": workspace["id"],
            "taskId": task_id,
            "instruction": instruction,
            "storySpecs": self._story_specs_for_tasks(agent_tasks),
            "agentTasks": agent_tasks,
            "teamSchedule": team_schedule,
            "agentAssignments": team_assignments,
            "productOwnerOutputId": product_owner_output_record["id"],
            "backlogArtifactId": backlog_artifact["id"],
            "preferredRuntime": effective_preferred_runtime,
            "qaCommands": qa_commands or [],
            "requireApproval": True,
            "resourceSelection": execution_resource,
            "metadata": {"loopId": loop["id"], "projectThreadId": thread.get("id")},
        }
        if run.rework_round:
            developer_payload["reworkRound"] = run.rework_round
        if (
            execution_resource.get("model")
            and execution_resource.get("preferredRuntime") == effective_preferred_runtime
        ):
            developer_payload["model"] = execution_resource["model"]
        failover_attempts: list[dict[str, Any]] = []
        try:
            runtime_result = self._run_with_failover(
                runtime=runtime,
                payload=developer_payload,
                run=run,
                attempts=failover_attempts,
                thread_id=thread_id,
            )
        except Exception as error:
            reason = str(redact_secrets(str(error)))
            if failover_attempts:
                reason = f"runtime_failover_exhausted: {reason}"
            blocked_result = self._block_run(
                loop,
                stage="runtime",
                reason=reason,
                actor=actor,
                details={
                    "status": "failed",
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": "failed",
                    "runtime": readiness,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                    "failoverAttempts": failover_attempts,
                },
                thread_id=thread_id,
            )
            evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
            if evidence_ref:
                resource_learning = self._record_resource_learning_best_effort(
                    project_id=project_id,
                    loop_id=loop["id"],
                    team_schedule=team_schedule,
                    runtime_result={
                        "status": "failed",
                        "reason": reason,
                        "runtime": readiness,
                        "workspaceId": workspace["id"],
                    },
                    evidence_ref=evidence_ref,
                    success=False,
                    rework=False,
                    quality_score=0.0,
                )
                blocked_result = self._attach_resource_learning_to_result(
                    blocked_result,
                    resource_learning,
                )
            return blocked_result
        run.runtime_result = runtime_result
        return None

    def _capture_review_evidence(self, run: _UserMessageRun) -> dict[str, Any] | None:
        """Captura el diff real del worktree como evidencia de review del runtime.

        Devuelve el resultado terminal (con learning) si el diff no se puede capturar o no
        hay archivos cambiados, o ``None`` para continuar al gate de QA.
        """
        project_id = run.project_id
        actor = run.actor
        loop = run.loop
        thread_id = run.thread_id
        effective_root = run.effective_root
        task_id = run.task_id
        workspace = run.workspace
        runtime_result = run.runtime_result
        agent_tasks = run.agent_tasks
        team_schedule = run.team_schedule
        runtime_status = str(runtime_result.get("status") or "failed")
        evidence_ids = self._external_evidence_ids(runtime_result)
        review = _review_from_runtime(runtime_result)
        review_capture_reason: str | None = None
        if workspace["isolationType"] == "git_worktree":
            try:
                diff = capture_git_diff(
                    Path(workspace["path"]),
                    connection=self.connection,
                    root=effective_root,
                    project_id=project_id,
                    workspace_id=workspace["id"],
                    task_id=f"{task_id}.review_diff",
                )
                review = _review_from_diff(diff)
            except Exception as error:
                review_capture_reason = (
                    "Product Loop review diff capture failed from the assigned git worktree: "
                    f"{redact_secrets(str(error))}"
                )
                review = {
                    "state": "capture_failed",
                    "changedFiles": [],
                    "branch": None,
                    "headCommit": None,
                    "diffStat": "",
                    "patch": "",
                    "patchSizeBytes": 0,
                    "truncated": False,
                    "toolCalls": [],
                    "policyDecisionIds": [],
                }
            if review["state"] != "captured":
                reason = (
                    review_capture_reason
                    or "Product Loop review diff could not be captured from the assigned git worktree."
                )
                blocked_result = self._block_run(
                    loop,
                    stage="review",
                    reason=reason,
                    actor=actor,
                    details={
                        "status": str(review.get("state") or "diff_unavailable"),
                        "reason": reason,
                        "workspaceId": workspace["id"],
                        "workspacePath": workspace["path"],
                        "runtimeStatus": runtime_status,
                        "runtimeResult": runtime_result,
                        "review": review,
                        "teamSchedule": team_schedule,
                        "agentTaskIds": [task["id"] for task in agent_tasks],
                    },
                    thread_id=thread_id,
                )
                evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
                if evidence_ref:
                    resource_learning = self._record_resource_learning_best_effort(
                        project_id=project_id,
                        loop_id=loop["id"],
                        team_schedule=team_schedule,
                        runtime_result=runtime_result,
                        evidence_ref=evidence_ref,
                        success=False,
                        rework=True,
                        quality_score=0.0,
                    )
                    blocked_result = self._attach_resource_learning_to_result(
                        blocked_result,
                        resource_learning,
                    )
                return blocked_result
        if not review["changedFiles"]:
            reason = (
                "Product Loop runtime completed without real changed files in the assigned worktree."
                if workspace["isolationType"] == "git_worktree"
                else "Product Loop runtime completed without changed files evidence."
            )
            blocked_result = self._block_run(
                loop,
                stage="review",
                reason=reason,
                actor=actor,
                details={
                    "status": str(review.get("state") or "diff_unavailable"),
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "runtimeResult": runtime_result,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
            evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
            if evidence_ref:
                resource_learning = self._record_resource_learning_best_effort(
                    project_id=project_id,
                    loop_id=loop["id"],
                    team_schedule=team_schedule,
                    runtime_result=runtime_result,
                    evidence_ref=evidence_ref,
                    success=False,
                    rework=True,
                    quality_score=0.0,
                )
                blocked_result = self._attach_resource_learning_to_result(
                    blocked_result,
                    resource_learning,
                )
            return blocked_result
        if workspace["isolationType"] == "git_worktree" and review.get("changedFiles"):
            # El trabajo capturado como evidencia se persiste como commit real en la rama de la HU,
            # para que el aterrizaje (merge/PR) tenga commits y no solo un patch. Best-effort: si el
            # commit falla, el loop continúa con la evidencia ya capturada como hasta ahora.
            try:
                commit_result = commit_workspace_changes(
                    workspace_path=Path(workspace["path"]),
                    message=f"AIDO product loop iteration: {task_id}",
                    connection=self.connection,
                    root=effective_root,
                    project_id=project_id,
                    workspace_id=workspace["id"],
                )
            except Exception as error:
                # Señal auxiliar: un fallo de commit nunca debe tumbar el loop; se registra y sigue.
                commit_result = {"status": "commit_failed", "reason": redact_secrets(str(error))}
            review = {**review, "commit": commit_result}
        run.runtime_status = runtime_status
        run.evidence_ids = evidence_ids
        run.review = review
        return None

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
                updated = self.transition_in_transaction(
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
            approval_effect = self._resolve_delivery_approval_action(
                loop=loop,
                decision=action,
                reason=feedback,
                actor=actor,
            )
            loop = self._record_delivery_approval_decision(loop, approval_effect)
            loop, landing_effect = self._land_delivered_work(loop)
            thread_effect = self._sync_delivery_feedback_thread_state(
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
                approval_effect = self._resolve_delivery_approval_action(
                    loop=loop,
                    decision=action,
                    reason=feedback,
                    actor=actor,
                )
                loop = self._record_delivery_approval_decision(loop, approval_effect)
                thread_effect = self._sync_delivery_feedback_thread_state(
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
            task = self.backlog.get_agent_task(target_id)
            self._assert_project_scope(task, project_id, label="Agent task")
            updated = self.backlog.update_agent_task(
                target_id,
                {
                    "status": "todo",
                    "metadata": _append_feedback_id(task.get("metadata"), feedback_id),
                },
            )
            approval_effect = self._resolve_delivery_approval_action(
                loop=loop,
                decision=action,
                reason=feedback,
                actor=actor,
            )
            loop = self._record_delivery_approval_decision(loop, approval_effect)
            thread_effect = self._sync_delivery_feedback_thread_state(
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
            loop, continuation_effect = self._queue_delivery_feedback_continuation(
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
        if action_key == "request_changes" and not effective_target_id:
            raise ProductLoopTransitionError("Feedback action request_changes requires a traceable target.")
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
