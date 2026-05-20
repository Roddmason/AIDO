from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from local_control_center.security_policy.models import PermissionGrantRecord
from local_control_center.shared.schemas import AuditEventRecord, EventRecord


SENSITIVE_JOB_KINDS = {
    "pipeline.start",
    "pipeline.retry",
    "pipeline.stage.retry",
}


JOB_STATUSES = {
    "queued",
    "running",
    "approval_required",
    "completed",
    "failed",
    "cancelled",
}
JobStatus = Literal["queued", "running", "approval_required", "completed", "failed", "cancelled"]


class JobCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")


class ApprovalReasonRequest(BaseModel):
    reason: str


class OptionalReasonRequest(BaseModel):
    reason: str = ""


class JobRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    kind: str
    status: JobStatus
    payload: dict[str, Any]
    lease_owner: str | None = Field(default=None, alias="leaseOwner")
    lease_expires_at: str | None = Field(default=None, alias="leaseExpiresAt")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class JobRunRecord(BaseModel):
    id: str
    job_id: str = Field(alias="jobId")
    provider_id: str | None = Field(default=None, alias="providerId")
    status: str
    started_at: str = Field(alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    summary: str
    metadata: dict[str, Any]


class ActionRequestRecord(BaseModel):
    id: str
    job_id: str = Field(alias="jobId")
    project_id: str = Field(alias="projectId")
    action_type: str = Field(alias="actionType")
    status: str
    risk_level: str = Field(alias="riskLevel")
    command: str
    payload: dict[str, Any]
    reason: str
    requested_at: str = Field(alias="requestedAt")
    decided_at: str | None = Field(default=None, alias="decidedAt")
    decided_by: str | None = Field(default=None, alias="decidedBy")


class JobMutationResponse(BaseModel):
    job: JobRecord
    audit_event: AuditEventRecord | None = Field(default=None, alias="auditEvent")
    events: list[EventRecord] = Field(default_factory=list)
    action_requests: list[ActionRequestRecord] = Field(default_factory=list, alias="actionRequests")
    action_request: ActionRequestRecord | None = Field(default=None, alias="actionRequest")
    permission_grant: PermissionGrantRecord | None = Field(default=None, alias="permissionGrant")


class JobsListResponse(BaseModel):
    jobs: list[JobRecord]
    events: list[EventRecord] = Field(default_factory=list)


class ApprovalsListResponse(BaseModel):
    action_requests: list[ActionRequestRecord] = Field(alias="actionRequests")
