"""Contrato y readiness del DeveloperAgent: esquema I/O y selección de runtime de edición.

Declara los esquemas de entrada/salida del agente desarrollador y sus runtimes elegibles —CLIs de
código (codex_cli/claude_code_cli) y modelos (openai_compatible/ollama)— con su orden de preferencia,
y calcula si hay un runtime ejecutable con capacidad de edición de código.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

DEVELOPER_AGENT_ID = "developer_agent"
DEVELOPER_AGENT_ALLOWED_TOOLS = ["shell", "openai_compatible", "ollama", "workspace_patch"]
DEVELOPER_AGENT_CLI_RUNTIMES = {"codex_cli", "claude_code_cli"}
DEVELOPER_AGENT_MODEL_RUNTIMES = {"openai_compatible", "ollama"}
DEVELOPER_AGENT_RUNTIME_ORDER = ["codex_cli", "claude_code_cli", "openai_compatible", "ollama"]


def developer_agent_contract() -> dict[str, Any]:
    """Describe el contrato del DeveloperAgent: esquemas I/O, tools permitidas y capacidades requeridas."""
    return {
        "id": DEVELOPER_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId", "instruction"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "instruction": {"type": "string"},
                "preferredRuntime": {"type": ["string", "null"]},
                "qaCommands": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "requireApproval": {"type": "boolean"},
                "maxCostUsd": {"type": ["number", "null"]},
                "approvalGrantId": {"type": ["string", "null"]},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["status", "runtimeResult", "diffSummary", "evidencePackage"],
            "properties": {
                "status": {"type": "string"},
                "reason": {"type": "string"},
                "runtimeResult": {"type": "object"},
                "qaResults": {"type": "array"},
                "diffSummary": {"type": "object"},
                "evidencePackage": {"type": "object"},
            },
        },
        "allowedTools": DEVELOPER_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": ["code_edit", "chat"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
    }


def _developer_runtime_reason(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id in DEVELOPER_AGENT_CLI_RUNTIMES and "code_edit" not in capabilities:
        return "CLI runtime does not advertise code_edit capability."
    if runtime_id == "ollama" and not runtime.get("models"):
        return "Ollama is reachable but no model is available for DeveloperAgent execution."
    return str(runtime.get("reason") or "Runtime is not executable for DeveloperAgent.")


def is_developer_runtime(runtime: dict[str, Any]) -> bool:
    """Indica si un runtime sirve como DeveloperAgent: CLI con code_edit o modelo con chat disponible."""
    runtime_id = str(runtime.get("id") or "")
    if not runtime.get("executable"):
        return False
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id in DEVELOPER_AGENT_CLI_RUNTIMES:
        return "code_edit" in capabilities
    if runtime_id == "openai_compatible":
        return "chat" in capabilities or not capabilities
    if runtime_id == "ollama":
        return bool("chat" in capabilities and runtime.get("models"))
    return False


def developer_agent_readiness(
    runtime_statuses: list[dict[str, Any]],
    *,
    preferred_runtime: str | None = None,
) -> dict[str, Any]:
    """Selecciona el runtime del DeveloperAgent (preferido si es válido, si no el de mayor prioridad).

    Returns:
        Estado de readiness con executable/status/reason, el runtime elegido, los candidatos y el contrato.
    """
    by_id = {str(runtime.get("id")): runtime for runtime in runtime_statuses}
    eligible = [runtime for runtime in runtime_statuses if is_developer_runtime(runtime)]
    ordered_eligible = sorted(
        eligible,
        key=lambda item: (
            DEVELOPER_AGENT_RUNTIME_ORDER.index(str(item["id"]))
            if str(item["id"]) in DEVELOPER_AGENT_RUNTIME_ORDER
            else len(DEVELOPER_AGENT_RUNTIME_ORDER)
        ),
    )
    selected = None
    if preferred_runtime:
        selected = by_id.get(preferred_runtime)
        if selected is None:
            return {
                "id": DEVELOPER_AGENT_ID,
                "executable": False,
                "status": "runtime_unavailable",
                "reason": f"Runtime provider is not catalogued: {preferred_runtime}",
                "selectedRuntimeId": None,
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": developer_agent_contract(),
            }
        if not is_developer_runtime(selected):
            return {
                "id": DEVELOPER_AGENT_ID,
                "executable": False,
                "status": "configuration_required"
                if not selected.get("configured")
                else "runtime_unavailable",
                "reason": _developer_runtime_reason(selected),
                "selectedRuntimeId": str(selected.get("id") or preferred_runtime),
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": developer_agent_contract(),
            }
    elif ordered_eligible:
        selected = ordered_eligible[0]

    if selected and is_developer_runtime(selected):
        selected_runtime_executable = is_developer_runtime(selected)
        return {
            "id": DEVELOPER_AGENT_ID,
            "executable": selected_runtime_executable,
            "status": "executable" if selected_runtime_executable else "runtime_unavailable",
            "reason": (
                "DeveloperAgent has a configured executable runtime."
                if selected_runtime_executable
                else _developer_runtime_reason(selected)
            ),
            "selectedRuntimeId": str(selected["id"]),
            "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
            "contract": developer_agent_contract(),
        }
    return {
        "id": DEVELOPER_AGENT_ID,
        "executable": False,
        "status": "runtime_unavailable",
        "reason": "No executable DeveloperAgent runtime is configured.",
        "selectedRuntimeId": None,
        "candidateRuntimeIds": [],
        "contract": developer_agent_contract(),
    }
