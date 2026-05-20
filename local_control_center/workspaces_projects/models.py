from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


WorkspaceIsolationType = Literal["directory", "git_worktree"]


class WorkspaceAllocateRequest(BaseModel):
    project_id: str = Field(alias="projectId")
    task_id: str = Field(alias="taskId")
    agent_id: str = Field(alias="agentId")
    reason: str = ""
    isolation_type: WorkspaceIsolationType = Field(default="directory", alias="isolationType")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    base_branch: str = Field(default="HEAD", alias="baseBranch")

    @field_validator("isolation_type", mode="before")
    @classmethod
    def normalize_isolation_type(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class WorkspaceArchiveRequest(BaseModel):
    reason: str = ""


class WorkspaceResponse(BaseModel):
    workspace: dict[str, Any]


class WorkspaceArchiveResponse(BaseModel):
    workspace: dict[str, Any]
    evidence_package: dict[str, Any] = Field(alias="evidencePackage")
