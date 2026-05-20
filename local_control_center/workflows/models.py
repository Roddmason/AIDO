from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from local_control_center.agents.contracts import AgentRunRecord
from local_control_center.evidence.models import EvidencePackageRecord
from local_control_center.jobs_approvals.models import JobRecord
from local_control_center.workspaces_projects.models import WorkspaceRecord


WorkflowKind = Literal["idea_to_pr", "project_discovery", "issue_to_pr", "qa_validation", "release_candidate"]


class WorkflowCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    kind: WorkflowKind = "idea_to_pr"
    title: str | None = None
    idea: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowStatusChangeRequest(BaseModel):
    reason: str = ""


class WorkflowRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    kind: str
    title: str
    status: str
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class WorkflowRunRecord(BaseModel):
    id: str
    workflow_id: str = Field(alias="workflowId")
    project_id: str = Field(alias="projectId")
    status: str
    started_at: str = Field(alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    metadata: dict[str, Any]


class WorkflowStepRecord(BaseModel):
    id: str
    workflow_run_id: str = Field(alias="workflowRunId")
    workflow_id: str = Field(alias="workflowId")
    project_id: str = Field(alias="projectId")
    name: str
    status: str
    agent_profile_id: str | None = Field(default=None, alias="agentProfileId")
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class WorkflowResponse(BaseModel):
    workflow: WorkflowRecord


class WorkflowsListResponse(BaseModel):
    workflows: list[WorkflowRecord]
    workflow_runs: list[WorkflowRunRecord] = Field(alias="workflowRuns")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")


class WorkflowDetailResponse(BaseModel):
    workflow: WorkflowRecord
    workflow_runs: list[WorkflowRunRecord] = Field(alias="workflowRuns")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")
    workspaces: list[WorkspaceRecord]
    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")
    jobs: list[JobRecord]
    agent_runs: list[AgentRunRecord] = Field(alias="agentRuns")


class WorkflowStartResponse(BaseModel):
    workflow: WorkflowRecord
    workflow_run: WorkflowRunRecord = Field(alias="workflowRun")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")
