from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ArtifactKind = Literal["execution_log", "screenshot", "test_report", "qa_report", "generic_artifact"]


class ArtifactCleanupRequest(BaseModel):
    dry_run: bool = Field(default=True, alias="dryRun")


class ArtifactRetentionPlanRequest(BaseModel):
    dry_run: bool = Field(default=True, alias="dryRun")
    now: str | None = None


class ArtifactRetentionActionRequest(BaseModel):
    reason: str
    action: str
    artifact_ids: list[str] = Field(alias="artifactIds")
    now: str | None = None


class EvidenceCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    agent_id: str | None = Field(default=None, alias="agentId")
    task_id: str = Field(default="task", alias="taskId")
    provider_id: str | None = Field(default=None, alias="providerId")
    model: str | None = None
    runtime_type: str | None = Field(default=None, alias="runtimeType")
    role: str | None = None
    job_id: str | None = Field(default=None, alias="jobId")
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
    qa_verdict: str = Field(default="not_started", alias="qaVerdict")


class ArtifactIngestRequest(BaseModel):
    kind: ArtifactKind
    name: str | None = None
    content: str | None = None
    content_base64: str | None = Field(default=None, alias="contentBase64")
    mime_type: str | None = Field(default=None, alias="mimeType")


class EvidencePackageRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    agent_id: str | None = Field(default=None, alias="agentId")
    task_id: str = Field(alias="taskId")
    test_plan: str = Field(alias="testPlan")
    acceptance_checklist: list[Any] = Field(alias="acceptanceChecklist")
    test_results: list[Any] = Field(alias="testResults")
    logs: list[Any]
    diff_refs: list[Any] = Field(alias="diffRefs")
    screenshot_refs: list[Any] = Field(alias="screenshotRefs")
    risk_notes: list[Any] = Field(alias="riskNotes")
    qa_verdict: str = Field(alias="qaVerdict")
    created_at: str = Field(alias="createdAt")


class TestResultRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str = Field(alias="evidencePackageId")
    command: str
    status: str
    duration_ms: int | None = Field(default=None, alias="durationMs")
    output_ref: str | None = Field(default=None, alias="outputRef")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ArtifactRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    kind: str
    path: str
    hash: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class ArtifactFileRecord(BaseModel):
    path: str
    size_bytes: int = Field(alias="sizeBytes")


class ExpiredArtifactRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    kind: str
    path: str
    hash: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    retention_status: str = Field(alias="retentionStatus")
    expires_at: str = Field(alias="expiresAt")


class ArtifactRetentionResultRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    kind: str
    path: str
    hash: str | None = None
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    retention_action: dict[str, Any] = Field(alias="retentionAction")


class EvidencePackageResponse(BaseModel):
    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")


class EvidenceListResponse(BaseModel):
    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")


class EvidenceDetailResponse(BaseModel):
    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")
    test_result_records: list[TestResultRecord] = Field(alias="testResultRecords")
    artifacts: list[ArtifactRecord]


class ArtifactResponse(BaseModel):
    artifact: ArtifactRecord


class ArtifactCleanupResponse(BaseModel):
    dry_run: bool = Field(alias="dryRun")
    artifact_root: str = Field(alias="artifactRoot")
    orphan_files: list[ArtifactFileRecord] = Field(alias="orphanFiles")
    deleted_files: list[ArtifactFileRecord] = Field(alias="deletedFiles")
    kept_referenced_files: int = Field(alias="keptReferencedFiles")


class ArtifactRetentionPlanResponse(BaseModel):
    dry_run: bool = Field(alias="dryRun")
    now: str
    expired_artifacts: list[ExpiredArtifactRecord] = Field(alias="expiredArtifacts")
    risk_ids: list[str] = Field(alias="riskIds")


class ArtifactRetentionActionResponse(BaseModel):
    action: str
    artifacts: list[ArtifactRetentionResultRecord]
