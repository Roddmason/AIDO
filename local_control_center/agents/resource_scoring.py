"""Pure scoring helpers that weigh cost, privacy, risk, context and latency factors for AI resource candidates.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from local_control_center.agents.ai_resource_manager import AIResourceRequest

REMOTE_LOCALITIES = {"remote"}


def weights_for_policy(policy_mode: str, risk: str) -> dict[str, float]:
    """Return the normalized scoring weights for a routing policy mode and task risk."""
    if policy_mode == "economy":
        return {
            "capability": 0.07,
            "quality": 0.10,
            "success": 0.12,
            "cost": 0.28,
            "cost_known": 0.10,
            "locality": 0.18,
            "context": 0.07,
            "rework": 0.04,
            "latency": 0.03,
            "task_risk": 0.01,
        }
    if policy_mode == "maximum":
        return {
            "capability": 0.07,
            "quality": 0.34,
            "success": 0.18,
            "cost": 0.03,
            "cost_known": 0.03,
            "locality": 0.04,
            "context": 0.16,
            "rework": 0.07,
            "latency": 0.04,
            "task_risk": 0.04,
        }
    if policy_mode == "critical" or risk in {"high", "critical"}:
        return {
            "capability": 0.08,
            "quality": 0.31,
            "success": 0.20,
            "cost": 0.04,
            "cost_known": 0.04,
            "locality": 0.04,
            "context": 0.12,
            "rework": 0.11,
            "latency": 0.02,
            "task_risk": 0.04,
        }
    if risk == "low":
        return {
            "capability": 0.08,
            "quality": 0.12,
            "success": 0.16,
            "cost": 0.26,
            "cost_known": 0.08,
            "locality": 0.16,
            "context": 0.08,
            "rework": 0.04,
            "latency": 0.03,
            "task_risk": 0.01,
        }
    return {
        "capability": 0.08,
        "quality": 0.22,
        "success": 0.18,
        "cost": 0.16,
        "cost_known": 0.06,
        "locality": 0.10,
        "context": 0.10,
        "rework": 0.06,
        "latency": 0.02,
        "task_risk": 0.02,
    }


def capability_match(required: list[str], capabilities: list[str]) -> float:
    """Return the fraction of required capabilities that a model provides."""
    required_values = {item.strip().lower() for item in required if item.strip()}
    if not required_values:
        return 1.0
    available = {item.strip().lower() for item in capabilities if item.strip()}
    return len(required_values & available) / len(required_values)


def cost_known_score(model: dict[str, Any], estimate: dict[str, Any]) -> float:
    """Score how well a model fits when its cost is known or the model is not remote."""
    if model["locality"] not in REMOTE_LOCALITIES:
        return 1.0
    return 1.0 if estimate["priceKnown"] else 0.0


def privacy_locality_fit(
    model: dict[str, Any],
    request: AIResourceRequest,
    risk: str,
    policy_mode: str,
) -> float:
    """Score how well a model locality matches the request privacy requirement."""
    if model["locality"] == "local":
        return 1.0
    if request.privacy_level == "local_private":
        return 0.0
    if request.privacy_level in {"private", "sensitive", "confidential"}:
        return 0.35
    if policy_mode == "economy":
        return 0.45
    if risk == "low":
        return 0.55
    return 0.75


def latency_penalty(latency_ms: Any) -> float:
    """Return the latency penalty that is subtracted from the candidate score."""
    if latency_ms is None:
        return 0.1
    return min(float(latency_ms) / 10_000, 1.0)


def bounded(value: float) -> float:
    """Clamp a value into the closed unit interval."""
    return max(0.0, min(value, 1.0))
