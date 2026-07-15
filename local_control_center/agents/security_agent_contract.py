"""Declares the SecurityAgent I/O contract and computes its runtime readiness.

Owns the SecurityAgent's stable identity, allowed tools, accepted verdicts, and the
input/output JSON schema, plus the logic that decides whether an optional model runtime
is eligible to assist. The deterministic controls are always executable; the model
runtime only augments them and never overrides the deterministic verdict.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from .provider_catalog import MODEL_PROVIDER_FAMILIES, REMOTE_MODEL_PROVIDER_FAMILIES

SECURITY_AGENT_ID = "security_agent"
SECURITY_AGENT_ALLOWED_TOOLS = [
    "shell",
    *sorted(MODEL_PROVIDER_FAMILIES),
]
SECURITY_AGENT_REMOTE_API_RUNTIMES = set(REMOTE_MODEL_PROVIDER_FAMILIES)
SECURITY_AGENT_MODEL_RUNTIMES = set(MODEL_PROVIDER_FAMILIES)
_LEGACY_REMOTE_RUNTIME_ORDER = [
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
]
SECURITY_AGENT_RUNTIME_ORDER = [
    "ollama",
    *_LEGACY_REMOTE_RUNTIME_ORDER,
    *sorted(REMOTE_MODEL_PROVIDER_FAMILIES - set(_LEGACY_REMOTE_RUNTIME_ORDER)),
]
SECURITY_AGENT_VERDICTS = {"passed", "risk", "blocked"}


def _security_runtime_family(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    provider_family = str(runtime.get("providerFamily") or "")
    return "ollama" if runtime_id == "ollama" or provider_family == "ollama" else provider_family


def security_agent_contract() -> dict[str, Any]:
    """Return the SecurityAgent contract: I/O schema, allowed tools, and required guarantees."""
    return {
        "id": SECURITY_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "diffArtifactId": {"type": ["string", "null"]},
                "commandCandidates": {"type": "array", "items": {"type": "object"}},
                "pathsToCheck": {"type": "array", "items": {"type": "string"}},
                "runModelAnalysis": {"type": "boolean"},
                "preferredRuntime": {"type": ["string", "null"]},
                "approvalGrantId": {"type": ["string", "null"]},
                "model": {"type": ["string", "null"]},
                "metadata": {"type": "object"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "status",
                "verdict",
                "findings",
                "filesScanned",
                "externalScanners",
                "findingsArtifact",
            ],
            "properties": {
                "status": {"type": "string", "enum": sorted(SECURITY_AGENT_VERDICTS)},
                "verdict": {"type": "string", "enum": sorted(SECURITY_AGENT_VERDICTS)},
                "findings": {"type": "array", "items": {"type": "object"}},
                "filesScanned": {"type": "array", "items": {"type": "object"}},
                "dependencyFiles": {"type": "array", "items": {"type": "object"}},
                "externalScanners": {"type": "array", "items": {"type": "object"}},
                "findingsArtifact": {"type": "object"},
                "evidencePackage": {"type": "object"},
            },
        },
        "allowedTools": SECURITY_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": [
            "deterministic_security_checks",
            "optional_external_security_scanners",
        ],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "deterministic_controls_with_optional_external_scanners",
    }


def is_security_model_runtime(runtime: dict[str, Any]) -> bool:
    """Return whether a runtime status qualifies as an optional SecurityAgent model assistant.

    Requires an eligible runtime id with an executable, and (for Ollama) at least one
    available chat model.
    """
    runtime_family = _security_runtime_family(runtime)
    if runtime_family not in SECURITY_AGENT_MODEL_RUNTIMES or not runtime.get("executable"):
        return False
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_family in SECURITY_AGENT_REMOTE_API_RUNTIMES:
        return "chat" in capabilities
    if runtime_family == "ollama":
        return "chat" in capabilities and bool(runtime.get("models"))
    return False


def security_agent_status(runtime_statuses: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize SecurityAgent readiness and pick a preferred optional model runtime.

    Deterministic controls report executable regardless of model availability; eligible
    model runtimes are ranked by the configured runtime order to select a candidate.
    """
    deterministic_controls_executable = bool(SECURITY_AGENT_ALLOWED_TOOLS)
    model_candidates = [runtime for runtime in runtime_statuses if is_security_model_runtime(runtime)]
    ordered = sorted(
        model_candidates,
        key=lambda item: (
            SECURITY_AGENT_RUNTIME_ORDER.index(_security_runtime_family(item))
            if _security_runtime_family(item) in SECURITY_AGENT_RUNTIME_ORDER
            else len(SECURITY_AGENT_RUNTIME_ORDER)
        ),
    )
    return {
        "id": SECURITY_AGENT_ID,
        "executable": deterministic_controls_executable,
        "status": "executable" if deterministic_controls_executable else "configuration_required",
        "reason": (
            "SecurityAgent deterministic controls are executable without model runtime; external scanners run only when their local CLIs are available/configured."
            if deterministic_controls_executable
            else "SecurityAgent deterministic controls are not configured."
        ),
        "selectedRuntimeId": str(ordered[0]["id"]) if ordered else None,
        "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered],
        "contract": security_agent_contract(),
    }
