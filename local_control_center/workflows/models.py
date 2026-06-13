from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from local_control_center.agents.contracts import AgentRunRecord, AgentToolCallRecord, ModelCallRecord
from local_control_center.evidence.models import ArtifactRecord, EvidencePackageRecord, TestResultRecord
from local_control_center.jobs_approvals.models import ActionRequestRecord, JobRecord, JobRunRecord
from local_control_center.security_policy.models import PermissionDecisionRecord
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
WorkflowStatus = Literal[
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "runtime_unavailable",
    "qa_failed",
    "evidence_ready",
    "approved_for_integration",
    "promotion_failed",
    "promoted_to_branch",
    "pr_created",
]
WorkflowRunStatus = Literal[
    "running",
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "runtime_unavailable",
    "qa_failed",
    "evidence_ready",
    "approved_for_integration",
    "promotion_failed",
    "promoted_to_branch",
    "pr_created",
]
WorkflowStepStatus = Literal["pending", "ready", "running", "completed", "failed", "blocked", "skipped"]
WorkflowRiskLevel = Literal["low", "medium", "high", "critical"]


class WorkflowCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    kind: WorkflowKind = "idea_to_pr"
    title: str | None = None
    idea: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowStatusChangeRequest(BaseModel):
    reason: str = ""


class PromotePatchToBranchRequest(BaseModel):
    reason: str
    branch_name: str | None = Field(default=None, alias="branchName")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    qa_commands: list[list[str]] | None = Field(default=None, alias="qaCommands")


class PullRequestCreateRequest(BaseModel):
    reason: str
    title: str | None = None
    base_branch: str | None = Field(default=None, alias="baseBranch")


class WorkflowGateAdvanceRequest(BaseModel):
    reason: str = ""
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")


class WorkflowRecord(BaseModel):
    id: str
    project_id: str = Field(alias="projectId")
    kind: WorkflowKind
    title: str
    status: WorkflowStatus
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class WorkflowRunRecord(BaseModel):
    id: str
    workflow_id: str = Field(alias="workflowId")
    project_id: str = Field(alias="projectId")
    status: WorkflowRunStatus
    started_at: str = Field(alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    metadata: dict[str, Any]


class WorkflowStepRecord(BaseModel):
    id: str
    workflow_run_id: str = Field(alias="workflowRunId")
    workflow_id: str = Field(alias="workflowId")
    project_id: str = Field(alias="projectId")
    name: str
    status: WorkflowStepStatus
    agent_profile_id: str | None = Field(default=None, alias="agentProfileId")
    role: str | None = None
    task_type: str | None = Field(default=None, alias="taskType")
    risk_level: WorkflowRiskLevel | None = Field(default=None, alias="riskLevel")
    model_mode: str | None = Field(default=None, alias="modelMode")
    manual_model_override: str | None = Field(default=None, alias="manualModelOverride")
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class WorkflowEventRecord(BaseModel):
    id: str
    workflow_id: str = Field(alias="workflowId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    project_id: str | None = Field(default=None, alias="projectId")
    type: str
    payload: dict[str, Any]
    severity: str
    created_at: str = Field(alias="createdAt")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    causation_id: str | None = Field(default=None, alias="causationId")


class WorkflowResponse(BaseModel):
    workflow: WorkflowRecord


class WorkflowsListResponse(BaseModel):
    workflows: list[WorkflowRecord]
    workflow_runs: list[WorkflowRunRecord] = Field(alias="workflowRuns")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")


class WorkflowRunDetail(BaseModel):
    workflow_run: WorkflowRunRecord = Field(alias="workflowRun")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")
    workflow_events: list[WorkflowEventRecord] = Field(default_factory=list, alias="workflowEvents")
    workspaces: list[WorkspaceRecord]
    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    test_result_records: list[TestResultRecord] = Field(default_factory=list, alias="testResultRecords")
    jobs: list[JobRecord]
    job_runs: list[JobRunRecord] = Field(default_factory=list, alias="jobRuns")
    action_requests: list[ActionRequestRecord] = Field(default_factory=list, alias="actionRequests")
    agent_runs: list[AgentRunRecord] = Field(alias="agentRuns")
    agent_tool_calls: list[AgentToolCallRecord] = Field(default_factory=list, alias="agentToolCalls")
    model_calls: list[ModelCallRecord] = Field(default_factory=list, alias="modelCalls")
    permission_decisions: list[PermissionDecisionRecord] = Field(default_factory=list, alias="permissionDecisions")


class WorkflowDetailResponse(BaseModel):
    workflow: WorkflowRecord
    workflow_runs: list[WorkflowRunRecord] = Field(alias="workflowRuns")
    workflow_steps: list[WorkflowStepRecord] = Field(alias="workflowSteps")
    workflow_events: list[WorkflowEventRecord] = Field(default_factory=list, alias="workflowEvents")
    workflow_run_details: list[WorkflowRunDetail] = Field(default_factory=list, alias="workflowRunDetails")
    workspaces: list[WorkspaceRecord]
    evidence_packages: list[EvidencePackageRecord] = Field(alias="evidencePackages")
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    test_result_records: list[TestResultRecord] = Field(default_factory=list, alias="testResultRecords")
    jobs: list[JobRecord]
    job_runs: list[JobRunRecord] = Field(default_factory=list, alias="jobRuns")
    action_requests: list[ActionRequestRecord] = Field(default_factory=list, alias="actionRequests")
    agent_runs: list[AgentRunRecord] = Field(alias="agentRuns")
    agent_tool_calls: list[AgentToolCallRecord] = Field(default_factory=list, alias="agentToolCalls")
    model_calls: list[ModelCallRecord] = Field(default_factory=list, alias="modelCalls")
    permission_decisions: list[PermissionDecisionRecord] = Field(default_factory=list, alias="permissionDecisions")


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


class IssueToPrRequest(IssueToPatchRequest):
    max_rework_attempts: int = Field(default=1, ge=0, le=3, alias="maxReworkAttempts")
    create_pull_request: bool = Field(default=False, alias="createPullRequest")
    build_scripts: list[str] = Field(default_factory=list, alias="buildScripts")
    quality_scripts: list[str] = Field(default_factory=list, alias="qualityScripts")
    docker_healthcheck: bool = Field(default=False, alias="dockerHealthcheck")


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
    pull_request: dict[str, Any] | None = Field(default=None, alias="pullRequest")


class IssueToPrResponse(IssueToPatchResponse):
    dag: dict[str, Any]
    gate_results: list[dict[str, Any]] = Field(alias="gateResults")
    rework: dict[str, Any]
    completion: dict[str, Any]
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowGateAdvanceResponse(BaseModel):
    workflow_step: WorkflowStepRecord = Field(alias="workflowStep")
    advanced: bool
    gate_state: str = Field(alias="gateState")
    reason: str
