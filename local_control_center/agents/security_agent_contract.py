from __future__ import annotations

from typing import Any


SECURITY_AGENT_ID = "security_agent"
SECURITY_AGENT_ALLOWED_TOOLS = ["shell", "openai_compatible", "ollama"]
SECURITY_AGENT_MODEL_RUNTIMES = {"openai_compatible", "ollama"}
SECURITY_AGENT_RUNTIME_ORDER = ["openai_compatible", "ollama"]
SECURITY_AGENT_VERDICTS = {"passed", "risk", "blocked"}


def security_agent_contract() -> dict[str, Any]:
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
            "required": ["status", "verdict", "findings", "filesScanned", "externalScanners", "findingsArtifact"],
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
        "requiredRuntimeCapabilities": ["deterministic_security_checks", "optional_external_security_scanners"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "verdictSource": "deterministic_controls_with_optional_external_scanners",
    }


def is_security_model_runtime(runtime: dict[str, Any]) -> bool:
    runtime_id = str(runtime.get("id") or "")
    if runtime_id not in SECURITY_AGENT_MODEL_RUNTIMES or not runtime.get("executable"):
        return False
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id == "openai_compatible":
        return "chat" in capabilities or not capabilities
    if runtime_id == "ollama":
        return "chat" in capabilities and bool(runtime.get("models"))
    return False


def security_agent_status(runtime_statuses: list[dict[str, Any]]) -> dict[str, Any]:
    deterministic_controls_executable = bool(SECURITY_AGENT_ALLOWED_TOOLS)
    model_candidates = [
        runtime
        for runtime in runtime_statuses
        if str(runtime.get("id") or "") in SECURITY_AGENT_MODEL_RUNTIMES and is_security_model_runtime(runtime)
    ]
    ordered = sorted(
        model_candidates,
        key=lambda item: SECURITY_AGENT_RUNTIME_ORDER.index(str(item["id"]))
        if str(item["id"]) in SECURITY_AGENT_RUNTIME_ORDER
        else len(SECURITY_AGENT_RUNTIME_ORDER),
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
