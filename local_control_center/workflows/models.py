from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from local_control_center.agents.contracts import AgentRunRecord
from local_control_center.evidence.models import EvidencePackageRecord
from local_control_center.jobs_approvals.models import JobRecord
from local_control_center.workspaces_projects.models import WorkspaceRecord


WorkflowKind = Literal[
    "idea_to_pr",
    "project_discovery",
    "issue_to_patch",
    "issue_to_pr",
    "qa_validation",
    "release_candidate",
    "pr_release_retro",
]


class WorkflowCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    kind: WorkflowKind = "idea_to_pr"
    title: str | None = None
    idea: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowStatusChangeRequest(BaseModel):
    reason: str = ""


class WorkflowGateAdvanceRequest(BaseModel):
    reason: str = ""
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")


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
    role: str | None = None
    task_type: str | None = Field(default=None, alias="taskType")
    risk_level: str | None = Field(default=None, alias="riskLevel")
    model_mode: str | None = Field(default=None, alias="modelMode")
    manual_model_override: str | None = Field(default=None, alias="manualModelOverride")
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


class IssueToPatchRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    title: str
    issue_text: str = Field(alias="issueText")
    target_path: str | None = Field(default=None, alias="targetPath")
    preferred_runtime: str | None = Field(default=None, alias="preferredRuntime")
    qa_commands: list[list[str]] = Field(default_factory=list, alias="qaCommands")
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd")
    require_approval: bool = Field(default=True, alias="requireApproval")


class IssueToPatchResponse(BaseModel):
    status: str
    reason: str
    workflow: WorkflowRecord
    workflow_run: WorkflowRunRecord = Field(alias="workflowRun")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")
    workspace: WorkspaceRecord
    job: JobRecord
    agent_run: AgentRunRecord = Field(alias="agentRun")
    evidence_package: EvidencePackageRecord = Field(alias="evidencePackage")
    runtime: dict[str, Any]
    runtime_result: dict[str, Any] = Field(alias="runtimeResult")
    qa_results: list[dict[str, Any]] = Field(alias="qaResults")
    diff_summary: dict[str, Any] = Field(alias="diffSummary")


class WorkflowGateAdvanceResponse(BaseModel):
    workflow_step: WorkflowStepRecord = Field(alias="workflowStep")
    advanced: bool
    gate_state: str = Field(alias="gateState")
    reason: str
