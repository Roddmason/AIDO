"""Bounded runtime ranking: Jev proposes, fresh AIDO checks authorize the candidate.

No provider preference or deterministic choice is supplied as a fallback.
@author Rodrigo Mason
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from local_control_center.settings.resolver import resolve_setting_value

from .models import DecisionCandidate, DecisionConstraints, DecisionContext, DecisionRequest, fingerprint
from .observers import _identity
from .service import ShadowDecisionEngine


def risk_review_evidence(receipt: dict, config) -> dict:
    """Risk consent cannot waive confidence, margin or the original allowlist."""
    if (
        receipt.get("status") != "completed"
        or receipt.get("mode") != "runtime_selection"
        or receipt.get("reasonCode") != "risk_requires_review"
        or receipt.get("effectiveDecision") is not None
        or receipt.get("configurationFingerprint") != config.configuration_fingerprint
        or receipt.get("recommendation") not in receipt.get("candidates", [])
        or not receipt.get("ranking")
        or receipt["ranking"][0] != receipt.get("recommendation")
        or receipt.get("confidence") is None
        or receipt["confidence"] < config.confidence_threshold
        or receipt.get("margin") is None
        or receipt["margin"] < config.margin_threshold
    ):
        raise ValueError("The Jev proposal does not satisfy the independent review gates.")
    return {
        "mode": "runtime_selection",
        "decisionId": receipt["decisionId"],
        "reasonCode": "risk_requires_review",
        "effectiveRisk": receipt["effectiveRisk"],
        "reviewRequired": True,
        "proposalIdentity": receipt["recommendation"],
    }


def rank_runtime_candidates(
    connection,
    *,
    config,
    candidates: list[dict],
    request,
    risk: str,
    routing_id: str | None,
    allow_inference: bool,
    revalidate: Callable[[str], str | None],
) -> tuple[str | None, dict]:
    """Preview stays local; only an execution request may ask Jev to rank the allowlist."""
    evidence = {"mode": "runtime_selection", "decisionId": None, "reasonCode": "pending"}
    if not allow_inference:
        return None, {**evidence, "reasonCode": "selection_requires_execution"}
    if len(candidates) > 255:
        return None, {**evidence, "reasonCode": "too_many_validated_candidates"}
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return None, {**evidence, "reasonCode": "selection_requires_sync_execution_boundary"}
    remote_enabled = request.allow_remote and request.privacy_level in {"public", "remote_allowed"}
    remote_enabled &= not resolve_setting_value(
        connection=connection, key="project.routing.forceLocal", project_id=request.project_id
    )
    remote_enabled &= all(
        resolve_setting_value(connection=connection, key=key, project_id=request.project_id) is True
        for key in ("runtime.remote.enabled", "project.runtime.remote.enabled")
    )
    task_type = next(
        (
            kind
            for kind in ("security", "architecture", "research", "review", "build", "bug", "feature")
            if kind in request.task_type
        ),
        "unknown",
    )
    options = tuple(
        DecisionCandidate(
            id=_identity(item),
            metadata={
                "provider": item.get("runtime"),
                "locality": item.get("locality"),
                "quality_score": item.get("qualityScore"),
                "context_window": item.get("contextWindow"),
                "free_tier": item.get("freeTier"),
            },
        )
        for item in sorted(candidates, key=_identity)
    )
    ranking_request = DecisionRequest(
        decision_type="runtime_model_ranking",
        candidates=options,
        context=DecisionContext(
            task_type=task_type,
            risk=risk,
            privacy_mode="metadata_only" if remote_enabled else "local_only",
            task_fingerprint=fingerprint([request.task_id, request.workflow_run_id]),
        ),
        constraints=DecisionConstraints(
            allowed_candidates=frozenset(item.id for item in options),
            deterministic_risk=risk,
            max_cost=request.budget_remaining_usd,
        ),
        effective_decision=None,
        project_id=request.project_id,
        execution_id=request.workflow_run_id,
        source_decision_id=routing_id,
    )
    try:
        receipt = asyncio.run(
            ShadowDecisionEngine(connection, config=config).select_runtime(
                ranking_request, revalidate=revalidate
            )
        )
    except Exception:
        return None, {**evidence, "reasonCode": "selection_evidence_unavailable"}
    if not receipt:
        return None, {**evidence, "reasonCode": "selection_disabled"}
    if receipt["reasonCode"] == "risk_requires_review":
        try:
            proposal = risk_review_evidence(receipt, config)
        except ValueError:
            proposal = {}
        if proposal:
            rejection = revalidate(receipt["recommendation"])
            if rejection:
                return None, {**evidence, "decisionId": receipt["decisionId"], "reasonCode": rejection}
            return None, proposal
    return receipt["effectiveDecision"], {
        **evidence,
        "decisionId": receipt["decisionId"],
        "reasonCode": receipt["reasonCode"],
        "effectiveRisk": receipt["effectiveRisk"],
        "confidence": receipt.get("confidence"),
        "margin": receipt.get("margin"),
    }
