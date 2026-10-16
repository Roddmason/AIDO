"""Runtime validation results for remediation actions, free of service state.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.agents.product_owner_agent_contract import product_owner_agent_readiness
from local_control_center.runtime_integrations.repository import is_ollama_runtime_id
from local_control_center.shared.redaction import redact_secrets


def runtime_status_for(providers: list[dict[str, Any]], runtime_id: str) -> dict[str, Any] | None:
    """Find the provider status for ``runtime_id`` (Ollama ids match each other)."""
    normalized = str(runtime_id or "").strip()
    if not normalized:
        return None
    for provider in providers:
        provider_id = str(provider.get("id") or "").strip()
        if provider_id == normalized:
            return provider
        if is_ollama_runtime_id(normalized) and is_ollama_runtime_id(provider_id):
            return provider
    return None


def validate_runtime_execution(
    *, runtime_id: str, providers: list[dict[str, Any]], product_owner: bool = False
) -> dict[str, Any]:
    """Build the ``validate_runtime`` execution result from provider statuses."""
    if product_owner:
        target = runtime_status_for(providers, runtime_id)
        preferred = str((target or {}).get("id") or runtime_id).strip() or None
        readiness = product_owner_agent_readiness(providers, preferred_runtime=preferred)
        selected_id = str(readiness.get("selectedRuntimeId") or runtime_id)
        return {
            "status": "completed" if readiness.get("executable") is True else "blocked",
            "action": "validate_runtime",
            "runtimeId": selected_id,
            "target": redact_secrets(runtime_status_for(providers, selected_id)),
            "providers": redact_secrets(providers),
            "reason": readiness["reason"],
        }
    if runtime_id:
        target = runtime_status_for(providers, runtime_id)
        if target is None:
            return {
                "status": "blocked",
                "action": "validate_runtime",
                "runtimeId": runtime_id,
                "providers": redact_secrets(providers),
                "reason": f"Runtime {runtime_id} is not available in runtime status.",
            }
        executable = target.get("executable") is True
        return {
            "status": "completed" if executable else "blocked",
            "action": "validate_runtime",
            "runtimeId": str(target.get("id") or runtime_id),
            "target": redact_secrets(target),
            "providers": redact_secrets(providers),
            "reason": "Target runtime is executable."
            if executable
            else str(target.get("reason") or f"Runtime {runtime_id} is not executable."),
        }
    executable_provider = next(
        (provider for provider in providers if provider.get("executable") is True), None
    )
    return {
        "status": "completed" if executable_provider is not None else "blocked",
        "action": "validate_runtime",
        "runtimeId": str((executable_provider or {}).get("id") or ""),
        "target": redact_secrets(executable_provider) if executable_provider is not None else None,
        "providers": redact_secrets(providers),
        "reason": "At least one runtime is executable."
        if executable_provider is not None
        else "No executable runtime is currently available.",
    }
