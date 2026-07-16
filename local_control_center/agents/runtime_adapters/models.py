"""Typed execution contracts shared by every runtime adapter implementation.

Defines the request/result models a broker-approved tool call is normalized into and the
`RuntimeAdapter` / `RuntimeExecutionAdapter` protocols implemented by concrete adapters
and broker-facing bridges.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.shared.time import utc_now


class RuntimeExecutionRequest(BaseModel):
    """Typed, workspace-scoped request handed to a runtime adapter for execution."""

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")
    job_id: str | None = Field(default=None, alias="jobId")
    agent_run_id: str | None = Field(default=None, alias="agentRunId")
    workspace_id: str = Field(alias="workspaceId")
    workspace_path: str = Field(alias="workspacePath")
    capability: str
    argv: Any = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")
    approval_grant_id: str | None = Field(default=None, alias="approvalGrantId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeExecutionResult(BaseModel):
    """Typed outcome of a runtime execution: status, exit code, and evidence/artifact ids."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    exit_code: int | None = Field(default=None, alias="exitCode")
    stdout_artifact_id: str | None = Field(default=None, alias="stdoutArtifactId")
    stderr_artifact_id: str | None = Field(default=None, alias="stderrArtifactId")
    output_artifact_id: str | None = Field(default=None, alias="outputArtifactId")
    evidence_package_id: str | None = Field(default=None, alias="evidencePackageId")
    started_at: str = Field(default_factory=utc_now, alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    reason: str | None = None
    redacted: bool = False


class RuntimeAdapter(Protocol):
    """Protocol for adapters that execute a typed `RuntimeExecutionRequest`."""

    adapter_id: str

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Execute a typed runtime request through a real adapter."""


class RuntimeExecutionAdapter(Protocol):
    """Protocol for broker-facing adapters that consume a raw tool call plus policy input."""

    def execute(self, *, tool_call: dict[str, Any], policy_input: dict[str, Any]) -> dict[str, Any]:
        """Execute a broker-approved runtime tool call."""
