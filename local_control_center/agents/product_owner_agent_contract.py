"""Contrato y readiness del ProductOwnerAgent: esquema I/O y selección de runtime real.

Declara el id, las tools/runtimes elegibles del agente de producto y su orden de preferencia
(CLIs reales codex_cli/claude_code_cli primero, luego modelos openai_compatible/ollama), y calcula
si hay un runtime ejecutable para analizar una idea o assessment y emitir un brief y backlog en JSON.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

PRODUCT_OWNER_AGENT_ID = "product_owner_agent"
PRODUCT_OWNER_AGENT_ALLOWED_TOOLS = [
    "shell",
    "ollama",
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
]
PRODUCT_OWNER_AGENT_CLI_RUNTIMES = {"codex_cli", "claude_code_cli"}
PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES = {
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
}
PRODUCT_OWNER_AGENT_MODEL_RUNTIMES = PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES | {"ollama"}
PRODUCT_OWNER_AGENT_RUNTIMES = PRODUCT_OWNER_AGENT_CLI_RUNTIMES | PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
PRODUCT_OWNER_AGENT_RUNTIME_ORDER = [
    "codex_cli",
    "claude_code_cli",
    "ollama",
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
]


def product_owner_agent_contract() -> dict[str, Any]:
    """Describe el contrato del ProductOwnerAgent: esquemas I/O, tools permitidas y runtimes preferidos."""
    return {
        "id": PRODUCT_OWNER_AGENT_ID,
        "inputSchema": {
            "type": "object",
            "required": ["projectId", "workspaceId", "taskId"],
            "properties": {
                "projectId": {"type": "string"},
                "workspaceId": {"type": "string"},
                "taskId": {"type": "string"},
                "idea": {"type": ["string", "null"]},
                "initiativeId": {"type": ["string", "null"]},
                "completenessThreshold": {"type": ["number", "null"]},
                "workflowContext": {"type": "object"},
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
                "summary",
                "confidence",
                "questions",
                "assumptions",
                "decisions",
                "productBriefPatch",
                "epics",
                "userStories",
                "risks",
                "recommendedNextAction",
            ],
            "properties": {
                "status": {"type": "string"},
                "summary": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "questions": {"type": "array", "items": {"type": "object"}},
                "assumptions": {"type": "array", "items": {"type": "object"}},
                "decisions": {"type": "array", "items": {"type": "object"}},
                "productBriefPatch": {"type": "object"},
                "epics": {"type": "array", "items": {"type": "object"}},
                "userStories": {"type": "array", "items": {"type": "object"}},
                "risks": {"type": "array", "items": {"type": "object"}},
                "recommendedNextAction": {"type": "string"},
            },
        },
        "allowedTools": PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": ["chat"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "outputSource": "validated_runtime_output_grounded_in_idea_or_assessment",
    }


def _product_owner_runtime_reason(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES and "code_edit" not in capabilities:
        return "CLI runtime does not advertise an executable capability for ProductOwnerAgent."
    if runtime_id == "ollama" and not runtime.get("models"):
        return "Ollama is reachable but no model is available for ProductOwnerAgent execution."
    return str(runtime.get("reason") or "Runtime is not executable for ProductOwnerAgent.")


def is_product_owner_runtime(runtime: dict[str, Any]) -> bool:
    """Indica si un runtime sirve como ProductOwnerAgent: CLI real ejecutable o modelo con chat disponible."""
    runtime_id = str(runtime.get("id") or "")
    if not runtime.get("executable"):
        return False
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        return "code_edit" in capabilities
    if runtime_id in PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES:
        return "chat" in capabilities or not capabilities
    if runtime_id == "ollama":
        return bool("chat" in capabilities and runtime.get("models"))
    return False


def product_owner_agent_readiness(
    runtime_statuses: list[dict[str, Any]],
    *,
    preferred_runtime: str | None = None,
) -> dict[str, Any]:
    """Selecciona el runtime del ProductOwnerAgent (preferido si es válido, si no el de mayor prioridad).

    Prioriza los runtimes CLI reales (codex_cli/claude_code_cli) y cae a modelos
    (openai_compatible/ollama) como camino de ejecución que produce el JSON validable.

    Returns:
        Estado de readiness con executable/status/reason, el runtime elegido, los candidatos y el contrato.
    """
    by_id = {str(runtime.get("id")): runtime for runtime in runtime_statuses}
    eligible = [runtime for runtime in runtime_statuses if is_product_owner_runtime(runtime)]
    ordered_eligible = sorted(
        eligible,
        key=lambda item: (
            PRODUCT_OWNER_AGENT_RUNTIME_ORDER.index(str(item["id"]))
            if str(item["id"]) in PRODUCT_OWNER_AGENT_RUNTIME_ORDER
            else len(PRODUCT_OWNER_AGENT_RUNTIME_ORDER)
        ),
    )
    selected = None
    if preferred_runtime:
        selected = by_id.get(preferred_runtime)
        if selected is None:
            return {
                "id": PRODUCT_OWNER_AGENT_ID,
                "executable": False,
                "status": "runtime_unavailable",
                "reason": f"Runtime provider is not catalogued: {preferred_runtime}",
                "selectedRuntimeId": None,
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": product_owner_agent_contract(),
            }
        if not is_product_owner_runtime(selected):
            return {
                "id": PRODUCT_OWNER_AGENT_ID,
                "executable": False,
                "status": "configuration_required"
                if not selected.get("configured")
                else "runtime_unavailable",
                "reason": _product_owner_runtime_reason(selected),
                "selectedRuntimeId": str(selected.get("id") or preferred_runtime),
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": product_owner_agent_contract(),
            }
    elif ordered_eligible:
        selected = ordered_eligible[0]

    if selected and is_product_owner_runtime(selected):
        return {
            "id": PRODUCT_OWNER_AGENT_ID,
            "executable": True,
            "status": "executable",
            "reason": "ProductOwnerAgent has a configured executable runtime.",
            "selectedRuntimeId": str(selected["id"]),
            "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
            "contract": product_owner_agent_contract(),
        }
    return {
        "id": PRODUCT_OWNER_AGENT_ID,
        "executable": False,
        "status": "runtime_unavailable",
        "reason": "No executable ProductOwnerAgent runtime is configured.",
        "selectedRuntimeId": None,
        "candidateRuntimeIds": [],
        "contract": product_owner_agent_contract(),
    }
