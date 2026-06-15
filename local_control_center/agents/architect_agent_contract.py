"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from typing import Any


ARCHITECT_AGENT_ID = "architect_agent"
ARCHITECT_AGENT_ALLOWED_TOOLS = ["openai_compatible", "ollama"]
ARCHITECT_AGENT_MODEL_RUNTIMES = {"openai_compatible", "ollama"}
ARCHITECT_AGENT_RUNTIME_ORDER = ["openai_compatible", "ollama"]
ARCHITECT_AGENT_VERDICTS = {"approved", "approved_with_risks", "changes_required", "rejected", "blocked"}


def architect_agent_contract() -> dict[str, Any]:
    return {
        "id": ARCHITECT_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId", "diffArtifactId", "workflowContext"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "diffArtifactId": {"type": "string"},
                "workflowContext": {"type": "object"},
                "relevantDocs": {"type": "array", "items": {"type": "object"}},
                "testResults": {"type": "array", "items": {"type": "object"}},
                "riskRegister": {"type": "array", "items": {"type": "object"}},
                "evidenceRefs": {"type": "array", "items": {"type": "string"}},
                "preferredRuntime": {"type": ["string", "null"]},
                "approvalGrantId": {"type": ["string", "null"]},
                "model": {"type": ["string", "null"]},
                "metadata": {"type": "object"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "verdict",
                "architectureFindings",
                "risks",
                "requiredChanges",
                "approvalRecommendation",
                "evidenceRefs",
            ],
            "properties": {
                "verdict": {"type": "string", "enum": sorted(ARCHITECT_AGENT_VERDICTS)},
                "architectureFindings": {"type": "array", "items": {"type": "object"}},
                "risks": {"type": "array", "items": {"type": "object"}},
                "requiredChanges": {"type": "array", "items": {"type": "object"}},
                "approvalRecommendation": {"type": "object"},
                "evidenceRefs": {"type": "array", "items": {"type": "string"}},
            },
        },
        "allowedTools": ARCHITECT_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": ["chat"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "validated_model_output_grounded_in_diff_and_evidence",
    }


def _architect_runtime_reason(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    if runtime_id == "ollama" and not runtime.get("models"):
        return "Ollama is reachable but no model is available for ArchitectAgent execution."
    return str(runtime.get("reason") or "Runtime is not executable for ArchitectAgent.")


def is_architect_runtime(runtime: dict[str, Any]) -> bool:
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in ARCHITECT_AGENT_MODEL_RUNTIMES or not runtime.get("executable"):
        return False
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id == "openai_compatible":
        return "chat" in capabilities or not capabilities
    if runtime_id == "ollama":
        return "chat" in capabilities and bool(runtime.get("models"))
    return False


def architect_agent_readiness(
    runtime_statuses: list[dict[str, Any]],
    *,
    preferred_runtime: str | None = None,
) -> dict[str, Any]:
    by_id = {str(runtime.get("id")): runtime for runtime in runtime_statuses}
    eligible = [runtime for runtime in runtime_statuses if is_architect_runtime(runtime)]
    ordered_eligible = sorted(
        eligible,
        key=lambda item: ARCHITECT_AGENT_RUNTIME_ORDER.index(str(item["id"]))
        if str(item["id"]) in ARCHITECT_AGENT_RUNTIME_ORDER
        else len(ARCHITECT_AGENT_RUNTIME_ORDER),
    )
    selected = None
    if preferred_runtime:
        selected = by_id.get(preferred_runtime)
        if selected is None:
            return {
                "id": ARCHITECT_AGENT_ID,
                "executable": False,
                "status": "runtime_unavailable",
                "reason": f"Runtime provider is not catalogued: {preferred_runtime}",
                "selectedRuntimeId": None,
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": architect_agent_contract(),
            }
        if not is_architect_runtime(selected):
            return {
                "id": ARCHITECT_AGENT_ID,
                "executable": False,
                "status": "configuration_required" if not selected.get("configured") else "runtime_unavailable",
                "reason": _architect_runtime_reason(selected),
                "selectedRuntimeId": str(selected.get("id") or preferred_runtime),
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": architect_agent_contract(),
            }
    elif ordered_eligible:
        selected = ordered_eligible[0]

    if selected and is_architect_runtime(selected):
        selected_runtime_executable = is_architect_runtime(selected)
        return {
            "id": ARCHITECT_AGENT_ID,
            "executable": selected_runtime_executable,
            "status": "executable" if selected_runtime_executable else "runtime_unavailable",
            "reason": (
                "ArchitectAgent has a configured executable model runtime."
                if selected_runtime_executable
                else _architect_runtime_reason(selected)
            ),
            "selectedRuntimeId": str(selected["id"]),
            "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
            "contract": architect_agent_contract(),
        }
    return {
        "id": ARCHITECT_AGENT_ID,
        "executable": False,
        "status": "runtime_unavailable",
        "reason": "No executable ArchitectAgent model runtime is configured.",
        "selectedRuntimeId": None,
        "candidateRuntimeIds": [],
        "contract": architect_agent_contract(),
    }
