"""Contratos Pydantic y enums del slice de evidencia (requests, responses y registros).

Define el vocabulario tipado compartido por el API y el repositorio: tipos de artefacto, fuentes
de evidencia, veredictos QA y estados de test, más los modelos de entrada/salida HTTP. Los alias
camelCase fijan la forma del JSON expuesto frente al snake_case interno de Python.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ArtifactKind = Literal[
    "execution_log",
    "screenshot",
    "test_report",
    "qa_report",
    "generic_artifact",
    "git_patch",
    "git_status",
    "security_findings",
    "model_call",
    "evidence_manifest",
    "devops_command_report",
    "devops_report",
    "security_report",
    "cli_stdout",
    "cli_stderr",
    "cli_runtime_log",
    "workspace_patch_manifest",
]
EvidenceSource = Literal[
    "operator_attested",
    "evidence_collected",
    "qa_passed_by_command",
    "verified_completion",
]
QAVerdict = Literal[
    "not_started",
    "passed",
    "failed",
    "blocked",
    "needs_human_review",
    "evidence_collected",
    "architecture_reviewed",
    "devops_risk",
    "devops_blocked",
    "security_passed",
    "security_blocked",
    "skipped_with_reason",
]
TestResultStatus = Literal[
    "passed",
    "failed",
    "completed",
    "denied",
    "allowed",
    "requires_approval",
    "approval_required",
    "blocked",
    "skipped",
    "skipped_with_reason",
    "error",
    "timed_out",
]


class ArtifactCleanupRequest(BaseModel):
    """Cuerpo para barrer artefactos huérfanos; por defecto en modo simulación."""

    dry_run: bool = Field(default=True, alias="dryRun")


class ArtifactRetentionPlanRequest(BaseModel):
    """Cuerpo para planificar la revisión de retención; `now` permite fijar el instante de corte."""

    dry_run: bool = Field(default=True, alias="dryRun")
    now: str | None = None


class ArtifactRetentionActionRequest(BaseModel):
    """Cuerpo para ejecutar una acción de retención (export/delete) auditada sobre artefactos expirados."""

    reason: str
    action: str
    artifact_ids: list[str] = Field(alias="artifactIds")
    now: str | None = None


class EvidenceCreateRequest(BaseModel):
    """Carga completa para crear un paquete de evidencia: enlaces de runtime, QA, costos y artefactos."""

    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_run_id: str | None = Field(default=None, alias="agentRunId")
    job_id: str | None = Field(default=None, alias="jobId")
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    runtime_id: str | None = Field(default=None, alias="runtimeId")
    task_id: str = Field(default="task", alias="taskId")
    provider_id: str | None = Field(default=None, alias="providerId")
    model: str | None = None
    runtime_type: str | None = Field(default=None, alias="runtimeType")
    role: str | None = None
    usage_ledger_id: str | None = Field(default=None, alias="usageLedgerId")
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    actual_cost_usd: float | None = Field(default=None, alias="actualCostUsd")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    rework: bool | None = None
    test_plan: str = Field(default="", alias="testPlan")
    acceptance_checklist: list[Any] = Field(default_factory=list, alias="acceptanceChecklist")
    test_results: list[dict[str, Any]] = Field(default_factory=list, alias="testResults")
    test_result_reports: list[dict[str, Any]] = Field(default_factory=list, alias="testResultReports")
    logs: list[Any] = Field(default_factory=list)
    diff_refs: list[Any] = Field(default_factory=list, alias="diffRefs")
    screenshot_refs: list[Any] = Field(default_factory=list, alias="screenshotRefs")
    risk_notes: list[Any] = Field(default_factory=list, alias="riskNotes")
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds")
    diff_summary: dict[str, Any] = Field(default_factory=dict, alias="diffSummary")
    runtime_health: dict[str, Any] = Field(default_factory=dict, alias="runtimeHealth")
    model_calls: list[dict[str, Any]] = Field(default_factory=list, alias="modelCalls")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, alias="toolCalls")
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list, alias="policyDecisions")
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    hashes: dict[str, str] = Field(default_factory=dict)
    evidence_source: EvidenceSource = Field(default="operator_attested", alias="evidenceSource")
    qa_verdict: QAVerdict = Field(default="not_started", alias="qaVerdict")


class ArtifactIngestRequest(BaseModel):
    """Cuerpo para ingerir un artefacto: contenido en texto o base64, con su tipo y nombre."""

    kind: ArtifactKind
    name: str | None = None
    content: str | None = None
    content_base64: str | None = Field(default=None, alias="contentBase64")
    mime_type: str | None = Field(default=None, alias="mimeType")


class EvidencePackageRecord(BaseModel):
    """Paquete de evidencia persistido tal como se devuelve al cliente."""

    id: str
    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    agent_run_id: str | None = Field(alias="agentRunId")
    job_id: str | None = Field(alias="jobId")
    workspace_id: str | None = Field(alias="workspaceId")
    runtime_id: str | None = Field(alias="runtimeId")
    task_id: str = Field(alias="taskId")
    test_plan: str = Field(alias="testPlan")
    acceptance_checklist: list[Any] = Field(alias="acceptanceChecklist")
    test_results: list[Any] = Field(alias="testResults")
    logs: list[Any]
    diff_refs: list[Any] = Field(alias="diffRefs")
    screenshot_refs: list[Any] = Field(alias="screenshotRefs")
    risk_notes: list[Any] = Field(alias="riskNotes")
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds")
    diff_summary: dict[str, Any] = Field(alias="diffSummary")
    runtime_health: dict[str, Any] = Field(alias="runtimeHealth")
    model_calls: list[dict[str, Any]] = Field(alias="modelCalls")
    tool_calls: list[dict[str, Any]] = Field(alias="toolCalls")
    policy_decisions: list[dict[str, Any]] = Field(alias="policyDecisions")
    approvals: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    hashes: dict[str, str]
    evidence_source: EvidenceSource = Field(alias="evidenceSource")
    qa_verdict: QAVerdict = Field(alias="qaVerdict")
    created_at: str = Field(alias="createdAt")


class TestResultRecord(BaseModel):
    """Resultado de un comando de test persistido y enlazado a su paquete."""

    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str = Field(alias="evidencePackageId")
    command: str
    status: TestResultStatus
    duration_ms: int | None = Field(default=None, alias="durationMs")
    output_ref: str | None = Field(default=None, alias="outputRef")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ArtifactRecord(BaseModel):
    """Artefacto persistido con su ruta, hash y metadata; puede estar sin enlazar a un paquete."""

    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    kind: ArtifactKind
    path: str
    hash: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ArtifactFileRecord(BaseModel):
    """Fichero físico de artefacto descrito por ruta y tamaño (usado en reportes de limpieza)."""

    path: str
    size_bytes: int = Field(alias="sizeBytes")


class ExpiredArtifactRecord(BaseModel):
    """Artefacto que superó su fecha de retención, anotado con estado y `expiresAt` para revisión."""

    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    kind: ArtifactKind
    path: str
    hash: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    retention_status: str = Field(alias="retentionStatus")
    expires_at: str = Field(alias="expiresAt")


class ArtifactRetentionResultRecord(BaseModel):
    """Artefacto tras aplicarle una acción de retención, con el detalle de la acción ejecutada."""

    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    kind: ArtifactKind
    path: str
    hash: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    retention_action: dict[str, Any] = Field(alias="retentionAction")


class EvidencePackageResponse(BaseModel):
    """Respuesta que envuelve un único paquete de evidencia."""

    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")


class EvidenceListResponse(BaseModel):
    """Respuesta con el listado de paquetes de evidencia."""

    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")


class EvidenceDetailResponse(BaseModel):
    """Detalle de un paquete junto a sus resultados de test y artefactos enlazados."""

    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")
    test_result_records: list[TestResultRecord] = Field(alias="testResultRecords")
    artifacts: list[ArtifactRecord]


class ArtifactResponse(BaseModel):
    """Respuesta que envuelve un único artefacto."""

    artifact: ArtifactRecord


class ArtifactCleanupResponse(BaseModel):
    """Resultado de la limpieza: huérfanos detectados, borrados y referenciados conservados."""

    dry_run: bool = Field(alias="dryRun")
    artifact_root: str = Field(alias="artifactRoot")
    orphan_files: list[ArtifactFileRecord] = Field(alias="orphanFiles")
    deleted_files: list[ArtifactFileRecord] = Field(alias="deletedFiles")
    kept_referenced_files: int = Field(alias="keptReferencedFiles")


class ArtifactRetentionPlanResponse(BaseModel):
    """Plan de retención: artefactos expirados y los riesgos de gobernanza abiertos para revisarlos."""

    dry_run: bool = Field(alias="dryRun")
    now: str
    expired_artifacts: list[ExpiredArtifactRecord] = Field(alias="expiredArtifacts")
    risk_ids: list[str] = Field(alias="riskIds")


class ArtifactRetentionActionResponse(BaseModel):
    """Resultado de una acción de retención y los artefactos sobre los que se aplicó."""

    action: str
    artifacts: list[ArtifactRetentionResultRecord]
