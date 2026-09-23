"""Effective project role restrictions and their execution-time seal.

@author Rodrigo Mason
"""

from __future__ import annotations

from dataclasses import replace

from local_control_center.decision_engine.models import fingerprint

from .repository import AgentsRepository


def effective_resource_profile(connection, profile_id, project_id):
    """Resolve persisted project overrides without replacing a manual profile."""
    if not profile_id:
        return None
    try:
        return AgentsRepository(connection).get_effective_agent_profile(
            profile_id=profile_id, project_id=project_id
        )
    except KeyError:
        return {"id": profile_id, "status": "missing"}


def profile_fingerprint(profile):
    """Seal the effective profile without exposing its configuration in the receipt."""
    return fingerprint(profile) if profile is not None else None


def apply_profile_limits(request, profile):
    """Intersect effective project limits before selection or any validation expense."""
    if profile is None:
        return request
    limits = {}
    for attribute, key in (
        ("budget_remaining_usd", "maxCostPerRun"),
        ("require_approval_over_usd", "requiresApprovalOverUsd"),
        ("context_token_limit", "maxTokensPerRun"),
    ):
        configured = profile.get(key)
        if configured is None or (key == "maxTokensPerRun" and configured <= 0):
            continue
        current = getattr(request, attribute)
        limits[attribute] = min(current, configured) if current is not None else configured
    return replace(request, **limits)


def profile_rejection(profile, model):
    """Intersect model candidates with the effective role's configured allowlists."""
    if profile is None:
        return None
    if profile.get("status") != "active":
        return "agent_profile_unavailable"
    for field, value in (("allowedProviders", model["providerId"]), ("allowedRuntimes", model["runtime"])):
        selectors = profile.get(field) or ["*"]  # Legacy empty allowlists mean unrestricted.
        if (
            "*" not in selectors
            and value not in selectors
            and not (field == "allowedRuntimes" and model["providerId"] in selectors)
        ):
            return "agent_profile_blocks_candidate"
    if model.get("locality") != "local" and not profile.get("allowRemote", True):
        return "agent_profile_blocks_remote"
    if model.get("runtime") == "cli" and not profile.get("allowCli", True):
        return "agent_profile_blocks_cli"
    if model.get("runtime") in {"api", "gateway"} and not profile.get("allowApi", True):
        return "agent_profile_blocks_api"
    return None


def selection_profile_changed(connection, seal, project_id):
    """Detect profile changes between ranking and the broker execution boundary."""
    profile_id = seal.get("agentProfileId")
    return bool(profile_id) and profile_fingerprint(
        effective_resource_profile(connection, profile_id, project_id)
    ) != seal.get("agentProfileFingerprint")
