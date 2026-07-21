"""Calcula disponibilidad efectiva de perfiles contra runtimes detectados.

La disponibilidad no se infiere de que un perfil exista ni de que declare ``runtimeMode``:
debe existir un proveedor candidato real que cumpla las capacidades requeridas por su
politica de runtime. Si no, se devuelve un bloqueo auditable con la razon del runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any


def _required_capabilities(profile: dict[str, Any]) -> list[str]:
    policy = profile.get("defaultRuntimePolicy") or {}
    capabilities = policy.get("requiredCapabilities") or []
    return [str(capability) for capability in capabilities if str(capability).strip()] or ["chat"]


def _ordered_provider_ids(values: Any) -> list[str]:
    seen: dict[str, None] = {}
    for candidate in values or []:
        value = str(candidate).strip()
        if value and value != "*":
            seen.setdefault(value, None)
    return list(seen)


def _candidate_provider_ids(profile: dict[str, Any], provider_statuses: list[dict[str, Any]]) -> list[str]:
    policy = profile.get("defaultRuntimePolicy") or {}
    allowed = profile.get("allowedProviders") or []
    if "*" in allowed:
        candidates = _ordered_provider_ids(policy.get("providerCandidates"))
        configured = sorted(
            {
                provider_id
                for provider in provider_statuses
                if (provider_id := str(provider.get("id") or "").strip()) and provider_id not in candidates
            }
        )
        return [*candidates, *configured]
    candidates = _ordered_provider_ids(allowed) or _ordered_provider_ids(policy.get("providerCandidates"))
    if candidates:
        return candidates
    return sorted(
        {
            provider_id
            for provider in provider_statuses
            if (provider_id := str(provider.get("id") or "").strip())
        }
    )


def _provider_satisfies(provider: dict[str, Any], required_capabilities: list[str]) -> bool:
    capabilities = set(provider.get("capabilities") or [])
    if "code_edit" in required_capabilities or "issue_to_patch" in required_capabilities:
        return bool(
            provider.get("canEditWorkspace") or ("code_edit" in capabilities and provider.get("executable"))
        )
    if "chat" in required_capabilities:
        return bool(provider.get("canRunPrompt") or provider.get("executable"))
    return bool(provider.get("executable") or provider.get("available"))


def runtime_availability(profile: dict[str, Any], provider_statuses: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the effective runtime availability for one agent profile."""
    required_capabilities = _required_capabilities(profile)
    candidates = _candidate_provider_ids(profile, provider_statuses)
    if profile.get("status") != "active":
        return {
            "status": "disabled",
            "available": False,
            "selectedProviderId": None,
            "candidateProviderIds": candidates,
            "blockedReason": "Agent profile is disabled.",
            "requiredCapabilities": required_capabilities,
        }
    if not candidates:
        return {
            "status": "configuration_required",
            "available": False,
            "selectedProviderId": None,
            "candidateProviderIds": [],
            "blockedReason": "Agent profile has no runtime provider candidates.",
            "requiredCapabilities": required_capabilities,
        }
    statuses_by_id = {str(provider.get("id")): provider for provider in provider_statuses}
    blockers: list[str] = []
    for candidate in candidates:
        provider = statuses_by_id.get(candidate)
        if provider is None:
            blockers.append(f"{candidate}: runtime provider is not catalogued")
            continue
        if _provider_satisfies(provider, required_capabilities):
            return {
                "status": "available",
                "available": True,
                "selectedProviderId": candidate,
                "candidateProviderIds": candidates,
                "blockedReason": "",
                "requiredCapabilities": required_capabilities,
            }
        reason = str(provider.get("reason") or "runtime provider is not executable")
        blockers.append(f"{candidate}: {reason}")
    return {
        "status": "blocked",
        "available": False,
        "selectedProviderId": None,
        "candidateProviderIds": candidates,
        "blockedReason": "No executable runtime provider is available. " + "; ".join(blockers),
        "requiredCapabilities": required_capabilities,
    }


def attach_runtime_availability(
    profiles: list[dict[str, Any]], provider_statuses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach runtime availability to each profile without mutating the repository records."""
    return [
        {**profile, "runtimeAvailability": runtime_availability(profile, provider_statuses)}
        for profile in profiles
    ]
