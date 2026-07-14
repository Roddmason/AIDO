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
    "git_status_failed",
    "git_branch_missing",
    "git_remote_missing",
    "gitleaks_missing",
    "gitleaks_failed",
    "qa_failed",
    "po_needs_input",
    "worker_not_running",
    "provider_missing_credentials",
    "provider_health_failed",
    "resource_manager_unconfigured",
    "resource_manager_privacy_blocked",
    "resource_manager_approval_required",
    "team_scheduler_failed",
    "technical_lead_planning_failed",
    "product_owner_output_invalid",
    "research_required",
    "workspace_root_missing",
    "workspace_allocation_failed",
    "review_diff_unavailable",
    "approval_unavailable",
    "resource_learning_failed",
    "project_assessment_failed",
    "functionality_memory_decision_required",
    "thread_similarity_decision_required",
    "thread_intake_decision_required",
)
BlockerType = Literal[
    "runtime_not_executable",
    "runtime_auth_missing",
    "runtime_output_invalid",
    "git_not_initialized",
    "git_dirty_tree",
    "git_status_failed",
    "git_branch_missing",
    "git_remote_missing",
    "gitleaks_missing",
    "gitleaks_failed",
    "qa_failed",
    "po_needs_input",
    "worker_not_running",
    "provider_missing_credentials",
    "provider_health_failed",
    "resource_manager_unconfigured",
    "resource_manager_privacy_blocked",
    "resource_manager_approval_required",
    "team_scheduler_failed",
    "technical_lead_planning_failed",
    "product_owner_output_invalid",
    "research_required",
    "workspace_root_missing",
    "workspace_allocation_failed",
    "review_diff_unavailable",
    "approval_unavailable",
    "resource_learning_failed",
    "project_assessment_failed",
    "functionality_memory_decision_required",
    "thread_similarity_decision_required",
    "thread_intake_decision_required",
]

REMEDIATION_ACTION_TYPES = (
    "open_settings_section",
    "validate_runtime",
    "switch_runtime",
    "continue_plan_only",
    "git_init",
    "add_remote",
    "create_branch",
    "checkout_branch",
    "run_gitleaks",
    "run_worker_once",
    "check_network_access",
    "answer_question",
    "approve_resource_decision",
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
    "add_remote",
    "create_branch",
    "checkout_branch",
    "run_gitleaks",
    "run_worker_once",
    "check_network_access",
    "answer_question",
    "approve_resource_decision",
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
    technical_reason: str = Field(default="", alias="technicalReason")
    primary: bool = False
    destructive: bool = False
    confirmation_required: bool = Field(default=False, alias="confirmationRequired")
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
