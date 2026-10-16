"""Classify a blocked Product Loop stage into a remediation blocker type.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.remediations.payloads import (
    decision_engine_failure,
    jev_confidence_below_threshold_failure,
    resource_selection_constraint_failure,
    runtime_risk_review_failure,
)
from local_control_center.remediations.resource_approvals import resource_decision_requires_approval


def has_actionable_pending_decision(details: dict[str, Any]) -> bool:
    """Return true when blocker details carry a pending decision the operator can act on."""
    pending_decisions = details.get("pendingDecisions") if isinstance(details, dict) else None
    if not isinstance(pending_decisions, list):
        return False
    for item in pending_decisions:
        if not isinstance(item, dict) or not str(item.get("decisionId") or "").strip():
            continue
        options = [str(option).strip() for option in item.get("options") or [] if str(option).strip()]
        if len(options) >= 2:
            return True
    return False


def blocker_type(*, stage: str, reason: str, details: dict[str, Any]) -> str:
    """Classify a blocked stage, reason and details into a remediation blocker type."""
    text = f"{stage} {reason} {details}".lower()
    reason_text = str(reason or "").lower()
    status = str(details.get("status") or "").lower()
    if stage == "review":
        return "review_diff_unavailable"
    if stage == "runtime_team":
        return "runtime_team_validation_expired"
    if stage == "gitleaks":
        if status == "configuration_required" or "not found" in text:
            return "gitleaks_missing"
        return "gitleaks_failed"
    if stage in {"qa", "qa_rework"}:
        return "qa_failed"
    if stage == "runtime":
        if any(token in reason_text for token in ("auth", "login", "not authenticated")):
            return "runtime_auth_missing"
        if details.get("executable") is False:
            return "runtime_not_executable"
        return "runtime_output_invalid"
    if stage == "resource_manager":
        if runtime_risk_review_failure(details) is not None or details.get("runtimeRiskReviewInvalid"):
            return "runtime_risk_review_required"
        if jev_confidence_below_threshold_failure(details) is not None:
            # Jev no tuvo confianza para desempatar candidatos ya validados: no es un runtime
            # inejecutable, ni una constraint de política, ni una validación vencida. Tiene su
            # propio tipo para que la tarjeta diga eso y no "el equipo necesita una prueba
            # reciente"; la acción es asignar un runtime al rol en el equipo del hilo.
            return "runtime_selection_ambiguous"
        if decision_engine_failure(details) is not None:
            return "decision_engine_unavailable"
        if resource_selection_constraint_failure(details) is not None:
            return "resource_manager_unconfigured"
        resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
        blockers = [
            item
            for item in resource_blockers or []
            if isinstance(item, dict) and isinstance(item.get("decision"), dict)
        ]
        if any(item["decision"].get("selected") is None for item in blockers):
            rejected = [
                rejected_item
                for item in blockers
                for rejected_item in item["decision"].get("rejected") or []
                if isinstance(rejected_item, dict)
            ]
            explicit_rejected = [item for item in rejected if item.get("profileSource") == "explicit"]
            relevant_rejected = explicit_rejected or rejected
            rejection_text = f"{reason_text} {relevant_rejected}".lower()
            if "credential" in rejection_text or "api key" in rejection_text:
                return "provider_missing_credentials"
            if "runtime_not_executable:" in rejection_text:
                return "runtime_not_executable"
            if "provider" in rejection_text and ("health" in rejection_text or "unhealthy" in rejection_text):
                return "provider_health_failed"
            if relevant_rejected and all(
                item.get("reason") == "privacy_blocks_remote" for item in relevant_rejected
            ):
                return "resource_manager_privacy_blocked"
            return "resource_manager_unconfigured"
        if "requires approval" in reason_text or any(
            resource_decision_requires_approval(item["decision"]) for item in blockers
        ):
            return "resource_manager_approval_required"
        return "resource_manager_unconfigured"
    if stage == "team_scheduler":
        return "team_scheduler_failed"
    if stage == "technical_lead":
        return "technical_lead_planning_failed"
    if stage == "resource_learning":
        return "resource_learning_failed"
    if stage == "product_owner_runtime":
        return "runtime_not_executable"
    if stage == "git":
        if details.get("remoteMissing") is True or "configured git remote" in reason_text:
            return "git_remote_missing"
        if status == "failed" or "status check failed" in reason_text:
            return "git_status_failed"
        if details.get("dirty") is True or "dirty" in reason_text:
            return "git_dirty_tree"
        if status == "configuration_required" or "not a git repository" in text:
            return "git_not_initialized"
        return "git_branch_missing"
    if stage == "worker":
        return "worker_not_running"
    if stage == "product_owner" and status in {"runtime_failed", "runtime_unavailable"}:
        runtime_result = details.get("runtimeResult")
        if isinstance(runtime_result, dict) and runtime_result.get("decision") == "deny":
            return "runtime_execution_denied"
        if isinstance(runtime_result, dict):
            http_status = runtime_result.get("httpStatus")
            if (
                str(runtime_result.get("execution") or "").startswith("runtime_adapter:")
                and isinstance(http_status, int)
                and 400 <= http_status <= 599
            ):
                return "runtime_execution_failed"
        return "runtime_not_executable"
    if stage == "product_owner" and status in {"persistence_failed", "failed_validation"}:
        return "product_owner_output_invalid"
    if stage == "product_owner" and (
        status == "needs_input"
        or bool(details.get("pendingDecisions"))
        or "question" in text
        or "input" in text
    ):
        if not has_actionable_pending_decision(details):
            return "product_owner_output_invalid"
        return "po_needs_input"
    if stage == "product_owner":
        return "product_owner_output_invalid"
    if stage == "backlog":
        return "product_owner_output_invalid"
    if stage == "research":
        return "research_required"
    if stage == "workspace_check":
        return "workspace_root_missing"
    if stage in {"product_owner_workspace", "workspace"}:
        return "workspace_allocation_failed"
    if stage == "approval":
        return "approval_unavailable"
    if stage == "project_assessment":
        return "project_assessment_failed"
    if stage == "functionality_memory":
        return "functionality_memory_decision_required"
    if stage == "thread_similarity":
        return "thread_similarity_decision_required"
    if stage == "thread_intake":
        return "thread_intake_decision_required"
    if "credential" in text or "api key" in text:
        return "provider_missing_credentials"
    if "provider" in text and ("health" in text or "unhealthy" in text):
        return "provider_health_failed"
    if "auth" in text or "login" in text or "not authenticated" in text:
        return "runtime_auth_missing"
    return "runtime_output_invalid"
