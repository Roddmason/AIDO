"""HTTP contracts for persisted blocker remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

BLOCKER_TYPES = (
    "runtime_not_executable",
    "runtime_auth_missing",
    "runtime_output_invalid",
    "git_not_initialized",
    "git_dirty_tree",
    "git_branch_missing",
    "gitleaks_missing",
    "gitleaks_failed",
    "qa_failed",
    "po_needs_input",
    "worker_not_running",
    "provider_missing_credentials",
    "provider_health_failed",
)
BlockerType = Literal[
    "runtime_not_executable",
    "runtime_auth_missing",
    "runtime_output_invalid",
    "git_not_initialized",
    "git_dirty_tree",
    "git_branch_missing",
    "gitleaks_missing",
    "gitleaks_failed",
    "qa_failed",
    "po_needs_input",
    "worker_not_running",
    "provider_missing_credentials",
    "provider_health_failed",
]

REMEDIATION_ACTION_TYPES = (
    "open_settings_section",
    "validate_runtime",
    "switch_runtime",
    "continue_plan_only",
    "git_init",
    "create_branch",
    "checkout_branch",
    "run_gitleaks",
    "run_worker_once",
    "answer_question",
    "retry_loop",
    "view_diff",
    "save_patch",
)
RemediationActionType = Literal[
    "open_settings_section",
    "validate_runtime",
    "switch_runtime",
    "continue_plan_only",
    "git_init",
    "create_branch",
    "checkout_branch",
    "run_gitleaks",
    "run_worker_once",
    "answer_question",
    "retry_loop",
    "view_diff",
    "save_patch",
]

REMEDIATION_STATUSES = ("pending", "resolved", "dismissed", "failed")
RemediationStatus = Literal["pending", "resolved", "dismissed", "failed"]


class RemediationActionRecord(BaseModel):
    """One user-repairable action created for a blocked thread or loop."""

    id: str
    project_id: str = Field(alias="projectId")
    thread_id: str = Field(alias="threadId")
    loop_id: str = Field(alias="loopId")
    stage: str
    blocker_type: BlockerType = Field(alias="blockerType")
    title: str
    description: str
    action_type: RemediationActionType = Field(alias="actionType")
    payload: dict[str, Any]
    status: RemediationStatus
    created_at: str = Field(alias="createdAt")
    resolved_at: str | None = Field(default=None, alias="resolvedAt")


class RemediationListResponse(BaseModel):
    """List of remediation actions for a thread."""

    remediations: list[RemediationActionRecord]


class RemediationExecuteRequest(BaseModel):
    """Optional execution override payload for a remediation action."""

    payload: dict[str, Any] = Field(default_factory=dict)


class RemediationExecuteResponse(BaseModel):
    """Result of attempting to execute one remediation action."""

    remediation: RemediationActionRecord
    execution: dict[str, Any]


class RemediationDismissResponse(BaseModel):
    """Result of dismissing one remediation action."""

    remediation: RemediationActionRecord
