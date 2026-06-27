"""Pydantic contracts for AIDO self-improvement proposals, lessons and performance records."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from local_control_center.jobs_approvals.models import ActionRequestRecord, JobRecord
from local_control_center.product_loop.models import (
    AgentTaskRecord,
    EpicRecord,
    ProductLoopRecord,
    UserStoryRecord,
)
from local_control_center.projects.models import ProjectRecord
from local_control_center.shared.schemas import AuditEventRecord
from local_control_center.workflows.models import WorkflowRecord
from local_control_center.workspaces_projects.models import WorkspaceRecord

SelfImprovementLessonScope = Literal["project", "global"]


class SelfImprovementProposalRecord(BaseModel):
    """Durable proposal linking the dedicated self-improvement backlog to source workspaces/PR flow."""

    id: str
    self_project_id: str = Field(alias="selfProjectId")
    source_project_id: str = Field(alias="sourceProjectId")
    title: str
    summary: str
    status: str
    goal_loop_id: str = Field(alias="goalLoopId")
    epic_id: str = Field(alias="epicId")
    story_id: str = Field(alias="storyId")
    task_id: str = Field(alias="taskId")
    workspace_id: str = Field(alias="workspaceId")
    workflow_id: str = Field(alias="workflowId")
    target_paths: list[str] = Field(alias="targetPaths")
    qa_commands: list[list[str]] = Field(alias="qaCommands")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SelfImprovementLessonRecord(BaseModel):
    """Recorded lesson. Global scope remains pending until evidence-backed approval is promoted."""

    id: str
    self_project_id: str = Field(alias="selfProjectId")
    source_project_id: str = Field(alias="sourceProjectId")
    proposal_id: str | None = Field(default=None, alias="proposalId")
    scope: SelfImprovementLessonScope
    title: str
    lesson: str
    status: str
    promotion_status: str = Field(alias="promotionStatus")
    evidence_package_ids: list[str] = Field(alias="evidencePackageIds")
    promotion_job_id: str = Field(alias="promotionJobId")
    promotion_action_request_id: str = Field(alias="promotionActionRequestId")
    approved_action_request_id: str = Field(alias="approvedActionRequestId")
    promoted_by: str = Field(alias="promotedBy")
    promoted_at: str | None = Field(default=None, alias="promotedAt")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class SelfImprovementPerformanceRecord(BaseModel):
    """Measured performance result tied to evidence."""

    id: str
    self_project_id: str = Field(alias="selfProjectId")
    source_project_id: str = Field(alias="sourceProjectId")
    proposal_id: str | None = Field(default=None, alias="proposalId")
    metric_name: str = Field(alias="metricName")
    value: float
    unit: str
    baseline_value: float | None = Field(default=None, alias="baselineValue")
    target_value: float | None = Field(default=None, alias="targetValue")
    evidence_package_id: str = Field(alias="evidencePackageId")
    recorded_by: str = Field(alias="recordedBy")
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")


class SelfImprovementProposalRequest(BaseModel):
    """Request to convert a proposed AIDO change into auditable self-improvement work."""

    source_project_id: str = Field(alias="sourceProjectId")
    title: str
    summary: str
    proposed_by: str = Field(default="operator", alias="proposedBy")
    qa_commands: list[list[str]] = Field(default_factory=list, alias="qaCommands")
    target_paths: list[str] = Field(default_factory=list, alias="targetPaths")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfImprovementLessonRequest(BaseModel):
    """Request to record a local/project lesson or global lesson candidate."""

    source_project_id: str = Field(alias="sourceProjectId")
    scope: SelfImprovementLessonScope
    title: str
    lesson: str
    evidence_package_ids: list[str] = Field(default_factory=list, alias="evidencePackageIds")
    proposal_id: str | None = Field(default=None, alias="proposalId")
    proposed_by: str = Field(default="operator", alias="proposedBy")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfImprovementPromoteLessonRequest(BaseModel):
    """Request to finalize promotion after the approval action has already been approved."""

    reason: str
    actor: str = "operator"


class SelfImprovementPerformanceRequest(BaseModel):
    """Request to record a measured performance datapoint backed by evidence."""

    source_project_id: str = Field(alias="sourceProjectId")
    metric_name: str = Field(alias="metricName")
    value: float
    unit: str
    evidence_package_id: str = Field(alias="evidencePackageId")
    proposal_id: str | None = Field(default=None, alias="proposalId")
    baseline_value: float | None = Field(default=None, alias="baselineValue")
    target_value: float | None = Field(default=None, alias="targetValue")
    recorded_by: str = Field(default="operator", alias="recordedBy")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfImprovementStateResponse(BaseModel):
    """Aggregate self-improvement state."""

    self_improvement_project: ProjectRecord | None = Field(alias="selfImprovementProject")
    proposals: list[SelfImprovementProposalRecord]
    lessons: list[SelfImprovementLessonRecord]
    performance_records: list[SelfImprovementPerformanceRecord] = Field(alias="performanceRecords")


class SelfImprovementProposalResponse(BaseModel):
    """Response after converting a proposal into existing AIDO SDLC records."""

    self_improvement_project: ProjectRecord = Field(alias="selfImprovementProject")
    source_project: ProjectRecord = Field(alias="sourceProject")
    proposal: SelfImprovementProposalRecord
    goal: ProductLoopRecord
    epic: EpicRecord
    story: UserStoryRecord
    task: AgentTaskRecord
    workspace: WorkspaceRecord
    workflow: WorkflowRecord
    audit_event: AuditEventRecord = Field(alias="auditEvent")


class SelfImprovementLessonResponse(BaseModel):
    """Response after recording or promoting a lesson."""

    lesson: SelfImprovementLessonRecord
    promotion_job: JobRecord | None = Field(default=None, alias="promotionJob")
    promotion_action_request: ActionRequestRecord | None = Field(default=None, alias="promotionActionRequest")
    audit_event: AuditEventRecord = Field(alias="auditEvent")


class SelfImprovementPerformanceResponse(BaseModel):
    """Response after recording a performance datapoint."""

    performance_record: SelfImprovementPerformanceRecord = Field(alias="performanceRecord")
    audit_event: AuditEventRecord = Field(alias="auditEvent")
