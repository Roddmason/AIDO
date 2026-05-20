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
    agent_id: str | None = Field(default=None, alias="agentId")
    task_id: str = Field(default="task", alias="taskId")
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


class EvidencePackageResponse(BaseModel):
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")


class EvidenceListResponse(BaseModel):
    evidence_packages: list[dict[str, Any]] = Field(alias="evidencePackages")


class EvidenceDetailResponse(BaseModel):
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
    test_result_records: list[dict[str, Any]] = Field(alias="testResultRecords")
    artifacts: list[dict[str, Any]]


class ArtifactResponse(BaseModel):
    artifact: dict[str, Any]


class ArtifactCleanupResponse(BaseModel):
    dry_run: bool = Field(alias="dryRun")
    artifact_root: str = Field(alias="artifactRoot")
    orphan_files: list[dict[str, Any]] = Field(alias="orphanFiles")
    deleted_files: list[dict[str, Any]] = Field(alias="deletedFiles")
    kept_referenced_files: int = Field(alias="keptReferencedFiles")


class ArtifactRetentionPlanResponse(BaseModel):
    dry_run: bool = Field(alias="dryRun")
    now: str
    expired_artifacts: list[dict[str, Any]] = Field(alias="expiredArtifacts")
    risk_ids: list[str] = Field(alias="riskIds")


class ArtifactRetentionActionResponse(BaseModel):
    action: str
    artifacts: list[dict[str, Any]]
