"""Pure helpers that derive and compare operator resource approvals for blocked runs.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.remediations.payloads import resource_policy_summary
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now


def resource_decision_requires_approval(decision: Any) -> bool:
    """Return true when a resource decision needs explicit operator approval."""
    if not isinstance(decision, dict):
        return False
    approval_required = decision.get("approvalRequired")
    if approval_required is True:
        return True
    if approval_required is False:
        return False
    policy_result = resource_policy_summary(decision.get("policyResult"))
    unknown_cost_policy = policy_result.get("unknownCostPolicy")
    return (
        isinstance(unknown_cost_policy, dict)
        and str(unknown_cost_policy.get("action") or "").strip() == "require_approval"
    )


def same_resource_approval(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Return true when two approvals target the same role, provider, model and runtime."""
    return all(
        str(current.get(key) or "") == str(candidate.get(key) or "")
        for key in ("role", "providerId", "model", "runtime")
    )


def single_resource_approval(*, action: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    """Build one redacted approval record from an action payload, or ``None`` when it is incomplete."""
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    role = str(payload.get("role") or "").strip()
    provider_id = str(payload.get("providerId") or selected.get("providerId") or "").strip()
    model = str(payload.get("model") or selected.get("model") or "").strip()
    runtime = str(payload.get("runtime") or selected.get("runtime") or "").strip()
    if not role or not provider_id or not model or not runtime:
        return None
    if not resource_decision_requires_approval(payload):
        return None
    approval = {
        "role": role,
        "providerId": provider_id,
        "model": model,
        "runtime": runtime,
        "taskId": str(payload.get("taskId") or "").strip(),
        "reason": str(payload.get("reason") or action.get("description") or "").strip(),
        "approvedBy": str(payload.get("approvedBy") or "remediation"),
        "approvedAt": utc_now(),
        "remediationActionId": action["id"],
    }
    for key in (
        "approvalRequired",
        "estimatedCostUsd",
        "usageStatus",
        "localVsRemote",
        "costTier",
        "budgetStop",
        "maxTokens",
    ):
        if key in payload:
            approval[key] = payload.get(key)
    policy_result = resource_policy_summary(payload.get("policyResult"))
    if policy_result:
        approval["policyResult"] = policy_result
    score_breakdown = payload.get("scoreBreakdown")
    if isinstance(score_breakdown, dict) and score_breakdown:
        approval["scoreBreakdown"] = score_breakdown
    return redact_secrets(approval)


def resource_approvals_from_payload(
    *, action: dict[str, Any], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Collect approvals from ``resourceApprovals`` or from the payload itself."""
    items = payload.get("resourceApprovals")
    if isinstance(items, list):
        approvals = [
            approval
            for item in items
            if isinstance(item, dict)
            for approval in [single_resource_approval(action=action, payload=item)]
            if approval is not None
        ]
        if approvals:
            return approvals
    approval = single_resource_approval(action=action, payload=payload)
    return [approval] if approval is not None else []


def resource_approvals_from_blockers(details: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive approval candidates from the ``resourceBlockers`` of a blocker."""
    resource_approvals: list[dict[str, Any]] = []
    resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else []
    for item in resource_blockers or []:
        if not isinstance(item, dict):
            continue
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        selected = decision.get("selected") if isinstance(decision.get("selected"), dict) else {}
        if not selected:
            continue
        policy_result = resource_policy_summary(decision.get("policyResult"))
        if not resource_decision_requires_approval(decision):
            continue
        approval = {
            "role": item.get("role"),
            "taskId": item.get("taskId"),
            "providerId": selected.get("providerId"),
            "model": selected.get("model"),
            "runtime": selected.get("runtime"),
            "selected": selected,
            "decisionReason": decision.get("decisionReason"),
        }
        for key in (
            "approvalRequired",
            "estimatedCostUsd",
            "usageStatus",
            "localVsRemote",
            "costTier",
            "budgetStop",
            "maxTokens",
        ):
            if key in decision:
                approval[key] = decision.get(key)
        if policy_result:
            approval["policyResult"] = policy_result
        score_breakdown = decision.get("scoreBreakdown")
        if isinstance(score_breakdown, dict) and score_breakdown:
            approval["scoreBreakdown"] = score_breakdown
        resource_approvals.append(approval)
    return resource_approvals


def approve_resource_decision_spec(details: dict[str, Any]) -> dict[str, Any] | None:
    """Build the ``approve_resource_decision`` action spec for a blocker, if any."""
    resource_approvals = resource_approvals_from_blockers(details)
    if not resource_approvals:
        return None
    return {
        "actionType": "approve_resource_decision",
        "title": "Approve selected AI resources",
        "description": "Approve the blocked model/runtime selections for the next Product Loop retry.",
        "payload": {"resourceApprovals": resource_approvals},
    }


def approved_resource_selections_from_durable(durable: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the approved resource selections stored in a durable run."""
    resource_approval = (
        durable.get("resourceApproval") if isinstance(durable.get("resourceApproval"), dict) else {}
    )
    approvals = resource_approval.get("approvedResourceSelections")
    if resource_approval.get("status") != "approved" or not isinstance(approvals, list):
        return []
    return [approval for approval in approvals if isinstance(approval, dict)]
