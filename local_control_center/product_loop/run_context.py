"""Contexto mutable de un run de mensaje de usuario compartido entre las fases del loop.

Vive aparte del coordinador para que las fases lo tipen sin importar la clase de 6 mil líneas.

@author Rodrigo Mason
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UserMessageRun:
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
    security_runner: Any | None = None

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
    research_resolution: dict[str, Any] = field(default_factory=dict)
    runtime_risk_review: dict[str, Any] = field(default_factory=dict)
    pending_thread_decisions: list[dict[str, Any]] = field(default_factory=list)
    po_artifact_ids: list[str] = field(default_factory=list)
    po_evidence: dict[str, Any] = field(default_factory=dict)
    product_owner_context: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    backlog_artifact: dict[str, Any] = field(default_factory=dict)
    agent_tasks: list[dict[str, Any]] = field(default_factory=list)
    team_schedule: dict[str, Any] = field(default_factory=dict)
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
    security_summary: dict[str, Any] = field(default_factory=dict)
    should_rework: bool = False
    constitution: dict[str, Any] = field(default_factory=dict)
    technical_plan: dict[str, Any] = field(default_factory=dict)
    diff_ref: dict[str, Any] = field(default_factory=dict)
    security_evidence: dict[str, Any] = field(default_factory=dict)
    resource_learning: dict[str, Any] = field(default_factory=dict)
    active_story_tasks: list[dict[str, Any]] | None = None
    story_noop: bool = False
    story_reviews: list[dict[str, Any]] = field(default_factory=list)
    story_qa_results: list[Any] = field(default_factory=list)
    story_evidence_ids: list[str] = field(default_factory=list)
    cumulative_patch_artifact_id: str = ""
