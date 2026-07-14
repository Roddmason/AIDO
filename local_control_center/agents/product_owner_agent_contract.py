"""Contrato y readiness del ProductOwnerAgent: esquema I/O y selección de runtime real.

Declara el id, las tools/runtimes elegibles del agente de producto y su orden de preferencia
(CLIs reales codex_cli/claude_code_cli primero, luego modelos openai_compatible/ollama), y calcula
si hay un runtime ejecutable para analizar una idea o assessment y emitir un brief y backlog en JSON.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from .autonomy_profiles import REVERSIBILITIES

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
PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS = 240


def _is_ollama_runtime(runtime: dict[str, Any]) -> bool:
    return str(runtime.get("id") or "") == "ollama" or runtime.get("providerFamily") == "ollama"


def _product_owner_runtime_family(runtime: dict[str, Any]) -> str:
    return "ollama" if _is_ollama_runtime(runtime) else str(runtime.get("providerFamily") or "")


def _product_owner_runtime_order_id(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    return (
        runtime_id
        if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES
        else _product_owner_runtime_family(runtime)
    )


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
                "assessment": {"type": "object"},
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
                "status": {
                    "type": "string",
                    "enum": [
                        "needs_input",
                        "questions_required",
                        "scope_is_clear",
                        "brief_ready",
                        "backlog_ready",
                        "completed",
                        "blocked",
                    ],
                },
                "summary": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "category",
                            "question",
                            "whyItMatters",
                            "blocking",
                            "options",
                            "recommendation",
                            "defaultDecision",
                            "confidence",
                        ],
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": [
                                    "scope",
                                    "users",
                                    "data",
                                    "integration",
                                    "compliance",
                                    "nonfunctional",
                                    "ux",
                                    "risk",
                                    "delivery",
                                ],
                            },
                            "question": {"type": "string", "minLength": 1},
                            "whyItMatters": {"type": "string", "minLength": 1},
                            "blocking": {"type": "boolean"},
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "items": {"type": "string", "minLength": 1},
                            },
                            "recommendation": {"type": "string", "minLength": 1},
                            "defaultDecision": {"type": "string", "minLength": 1},
                            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        },
                    },
                },
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["statement"],
                        "properties": {
                            "statement": {"type": "string", "minLength": 1},
                            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                            "validation": {"type": "string"},
                        },
                    },
                },
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title", "status", "confidence"],
                        "properties": {
                            "title": {"type": "string"},
                            "question": {"type": "string"},
                            "rationale": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["open", "proposed", "resolved", "accepted"],
                            },
                            "options": {"type": "array", "items": {"type": "string"}},
                            "recommendation": {"type": "string"},
                            "category": {
                                "type": "string",
                                "description": "Decision domain, e.g. product, technical, architecture, database, security.",
                            },
                            "impact": {"type": "string", "enum": ["low", "medium", "high", "critical", ""]},
                            "requiresResearch": {"type": "boolean"},
                            "reversibility": {
                                "type": "string",
                                "enum": sorted(REVERSIBILITIES),
                            },
                            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        },
                    },
                },
                "productBriefPatch": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "summary": {"type": "string"},
                        "problemStatement": {"type": "string"},
                        "goals": {"type": "array", "items": {"type": "string"}},
                        "targetUsers": {"type": "array", "items": {"type": "string"}},
                        "successMetrics": {"type": "array", "items": {"type": "string"}},
                        "scope": {"type": "string"},
                        "outOfScope": {"type": "string"},
                    },
                },
                "epics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title"],
                        "properties": {
                            "title": {"type": "string", "minLength": 1},
                            "description": {"type": "string"},
                        },
                    },
                },
                "userStories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "epicTitle",
                            "title",
                            "asA",
                            "iWant",
                            "soThat",
                            "acceptanceCriteria",
                        ],
                        "properties": {
                            "epicTitle": {"type": "string", "minLength": 1},
                            "title": {"type": "string", "minLength": 1},
                            "asA": {"type": "string", "minLength": 1},
                            "iWant": {"type": "string", "minLength": 1},
                            "soThat": {"type": "string", "minLength": 1},
                            "businessValue": {"type": "string", "enum": ["low", "medium", "high"]},
                            "acceptanceCriteria": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
                "risks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["description"],
                        "properties": {
                            "severity": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                            },
                            "description": {"type": "string", "minLength": 1},
                            "mitigation": {"type": "string"},
                        },
                    },
                },
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
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES and runtime.get(
        "productOwnerExecutable"
    ) is False:
        return str(
            runtime.get("reason")
            or "CLI runtime has not passed the ProductOwnerAgent-specific safety contract."
        )
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES and (
        "chat" not in capabilities or runtime.get("canRunPrompt") is False
    ):
        return "CLI runtime does not advertise the chat prompt capability required by ProductOwnerAgent."
    if _is_ollama_runtime(runtime) and not runtime.get("models"):
        return "Ollama is reachable but no model is available for ProductOwnerAgent execution."
    return str(runtime.get("reason") or "Runtime is not executable for ProductOwnerAgent.")


def is_product_owner_runtime(runtime: dict[str, Any]) -> bool:
    """Indica si un runtime sirve como ProductOwnerAgent: CLI real ejecutable o modelo con chat disponible."""
    runtime_id = str(runtime.get("id") or "")
    runtime_family = _product_owner_runtime_family(runtime)
    capabilities = set(runtime.get("capabilities") or [])
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        product_owner_executable = runtime.get("productOwnerExecutable")
        prompt_executable = (
            bool(product_owner_executable)
            if product_owner_executable is not None
            else bool(runtime.get("canRunPrompt", runtime.get("executable")))
        )
        return prompt_executable and "chat" in capabilities
    if not runtime.get("executable"):
        return False
    if runtime_family in PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES:
        return "chat" in capabilities
    if _is_ollama_runtime(runtime):
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
            PRODUCT_OWNER_AGENT_RUNTIME_ORDER.index(_product_owner_runtime_order_id(item))
            if _product_owner_runtime_order_id(item) in PRODUCT_OWNER_AGENT_RUNTIME_ORDER
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
