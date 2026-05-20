from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class JobMutationResponse(BaseModel):
    job: dict[str, Any]
    audit_event: dict[str, Any] | None = Field(default=None, alias="auditEvent")
    events: list[dict[str, Any]] = Field(default_factory=list)
    action_requests: list[dict[str, Any]] = Field(default_factory=list, alias="actionRequests")
    action_request: dict[str, Any] | None = Field(default=None, alias="actionRequest")
    permission_grant: dict[str, Any] | None = Field(default=None, alias="permissionGrant")


class JobsListResponse(BaseModel):
    jobs: list[dict[str, Any]]
    events: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalsListResponse(BaseModel):
    action_requests: list[dict[str, Any]] = Field(alias="actionRequests")
