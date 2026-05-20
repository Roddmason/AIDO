from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


WorkflowKind = Literal["idea_to_pr", "project_discovery", "issue_to_pr", "qa_validation", "release_candidate"]


class WorkflowCreateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    kind: WorkflowKind = "idea_to_pr"
    title: str | None = None
    idea: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowStatusChangeRequest(BaseModel):
    reason: str = ""


class WorkflowResponse(BaseModel):
    workflow: dict[str, Any]


class WorkflowsListResponse(BaseModel):
    workflows: list[dict[str, Any]]
    workflow_runs: list[dict[str, Any]] = Field(alias="workflowRuns")
    workflow_steps: list[dict[str, Any]] = Field(alias="workflowSteps")


class WorkflowDetailResponse(BaseModel):
    workflow: dict[str, Any]
    workflow_runs: list[dict[str, Any]] = Field(alias="workflowRuns")
    workflow_steps: list[dict[str, Any]] = Field(alias="workflowSteps")
    workspaces: list[dict[str, Any]]
    evidence_packages: list[dict[str, Any]] = Field(alias="evidencePackages")
    jobs: list[dict[str, Any]]
    agent_runs: list[dict[str, Any]] = Field(alias="agentRuns")


class WorkflowStartResponse(BaseModel):
    workflow: dict[str, Any]
    workflow_run: dict[str, Any] = Field(alias="workflowRun")
    workflow_steps: list[dict[str, Any]] = Field(alias="workflowSteps")
