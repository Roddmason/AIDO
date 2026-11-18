"""Effective role limits must apply before any automatic validation expense."""

from local_control_center.agents import agent_resource_policy
from local_control_center.agents.ai_resource_manager import AIResourceRequest


def test_project_limits_intersect_request_and_preserve_zero():
    request = AIResourceRequest(
        task_type="chat", budget_remaining_usd=5.0, require_approval_over_usd=2.0, context_token_limit=1000
    )
    effective = agent_resource_policy.apply_profile_limits(
        request,
        {
            "maxCostPerRun": 0,
            "requiresApprovalOverUsd": 0,
            "maxTokensPerRun": 500,
        },
    )
    assert effective.budget_remaining_usd == 0
    assert effective.require_approval_over_usd == 0
    assert effective.context_token_limit == 500
    assert request.budget_remaining_usd == 5.0


def test_profile_cannot_increase_existing_request_limits():
    request = AIResourceRequest(
        task_type="chat", budget_remaining_usd=1.0, require_approval_over_usd=0.5, context_token_limit=1000
    )
    effective = agent_resource_policy.apply_profile_limits(
        request,
        {
            "maxCostPerRun": 10,
            "requiresApprovalOverUsd": 2,
            "maxTokensPerRun": 0,
        },
    )
    assert effective.budget_remaining_usd == 1.0
    assert effective.require_approval_over_usd == 0.5
    assert effective.context_token_limit == 1000
