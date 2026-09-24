"""Contrato y readiness del ProductOwnerAgent: esquema I/O y selección de runtime real.

Declara el id, las tools/runtimes elegibles del agente de producto y su orden de preferencia:
cuentas gratuitas declaradas por el operador y Ollama primero, luego CLIs reales y finalmente APIs
pagadas.
Tambien calcula si existe un runtime ejecutable para emitir el brief y backlog en JSON.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.security_policy.policy_engine import MODEL_RUNTIME_REASON, MODEL_RUNTIME_TOOLS
from local_control_center.shared.redaction import redact_secrets

from .autonomy_profiles import REVERSIBILITIES
from .provider_catalog import MODEL_PROVIDER_FAMILIES, REMOTE_MODEL_PROVIDER_FAMILIES
from .runtime_selection import is_ollama_runtime, runtime_provider_family

PRODUCT_OWNER_AGENT_ID = "product_owner_agent"
# Claves que delatan una historia técnica (tarea por rol de agente) en vez de valor de usuario. Viven
# aquí, junto al esquema, como regla de contrato: la usan tanto el `description` del esquema como el
# validador, de modo que ambos comparten una sola fuente y no pueden derivar.
TECHNICAL_STORY_KEYS = frozenset({"role", "agentRole", "taskRole", "technicalTask", "implementationTask"})
PRODUCT_OWNER_AGENT_CLI_RUNTIMES = {"codex_cli", "claude_code_cli"}
# Being catalogued does not authorize the ProductOwner model-call operation.
# Keep readiness and resource selection inside the executor's existing policy.
PRODUCT_OWNER_AGENT_MODEL_RUNTIMES = set(MODEL_PROVIDER_FAMILIES & MODEL_RUNTIME_TOOLS)
PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES = set(REMOTE_MODEL_PROVIDER_FAMILIES & MODEL_RUNTIME_TOOLS)
PRODUCT_OWNER_AGENT_ALLOWED_TOOLS = ["shell", *sorted(PRODUCT_OWNER_AGENT_MODEL_RUNTIMES)]
PRODUCT_OWNER_AGENT_RUNTIMES = PRODUCT_OWNER_AGENT_CLI_RUNTIMES | PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
_LEGACY_REMOTE_RUNTIME_ORDER = [
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
]
PRODUCT_OWNER_AGENT_RUNTIME_ORDER = [
    "gemini",
    "ollama",
    "codex_cli",
    "claude_code_cli",
    *_LEGACY_REMOTE_RUNTIME_ORDER,
    *sorted(REMOTE_MODEL_PROVIDER_FAMILIES - {"gemini", *_LEGACY_REMOTE_RUNTIME_ORDER}),
]
PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS = 240


def _product_owner_runtime_order_id(runtime: dict[str, Any]) -> str:
    runtime_id = str(runtime.get("id") or "")
    return runtime_id if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES else runtime_provider_family(runtime)


def _product_owner_runtime_cost_rank(runtime: dict[str, Any]) -> int:
    provider_family = runtime_provider_family(runtime)
    declared_free = str(runtime.get("pricingMode") or "") == "free" and (
        provider_family != "gemini" or runtime.get("freeTierDeclaredByOperator") is True
    )
    self_hosted = runtime.get("selfHostedInference") is True
    if declared_free or self_hosted or is_ollama_runtime(runtime):
        return 0
    if str(runtime.get("id") or "") in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
        return 1
    return 2


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
                "epicId": {"type": ["string", "null"]},
                "completenessThreshold": {"type": ["number", "null"]},
                "workflowContext": {"type": "object"},
                "assessment": {"type": "object"},
                "preferredRuntime": {"type": ["string", "null"]},
                "approvalGrantId": {"type": ["string", "null"]},
                "maxRuntimeAttempts": {"type": "integer", "minimum": 1, "maximum": 2, "default": 2},
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
                "summary": {"type": "string", "minLength": 1},
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
                            "recommendation": {
                                "type": "string",
                                "minLength": 1,
                                "description": (
                                    "Copy of one element of this question's options array, character "
                                    "for character. Never a prefix, a paraphrase, an index or a new value."
                                ),
                            },
                            "defaultDecision": {
                                "type": "string",
                                "minLength": 1,
                                "description": (
                                    "Copy of one element of this question's options array, character "
                                    "for character. Never a prefix, a paraphrase, an index or a new value."
                                ),
                            },
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
                        "description": (
                            "A user story is end-user value (asA/iWant/soThat), never a technical task. "
                            "Do not add any of these keys: " + ", ".join(sorted(TECHNICAL_STORY_KEYS)) + "."
                        ),
                        "required": [
                            "epicTitle",
                            "title",
                            "asA",
                            "iWant",
                            "soThat",
                            "acceptanceCriteria",
                        ],
                        "properties": {
                            "epicTitle": {
                                "type": "string",
                                "minLength": 1,
                                "description": (
                                    "Copy of one epics[].title from this same response, character for "
                                    "character. A story may not reference an epic that is not declared."
                                ),
                            },
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
                "recommendedNextAction": {"type": "string", "minLength": 1},
            },
        },
        "allowedTools": PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
        "requiredRuntimeCapabilities": ["chat"],
        "requiredWorkspace": True,
        "requiredEvidence": True,
        "outputSource": "validated_runtime_output_grounded_in_idea_or_assessment",
    }


def _product_owner_runtime_reason(runtime: dict[str, Any]) -> str:
    reasons = list(runtime.get("blockingReasons") or [])
    reasons.extend((runtime.get("compatibility") or {}).get("blockingReasons") or [])
    if reasons:
        return str(redact_secrets("; ".join(dict.fromkeys(reasons))))
    runtime_id = str(runtime.get("id") or "")
    capabilities = set(runtime.get("capabilities") or [])
    if (
        runtime_id not in PRODUCT_OWNER_AGENT_CLI_RUNTIMES
        and runtime_provider_family(runtime) not in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
    ):
        return f"ProductOwnerAgent model execution is limited to {MODEL_RUNTIME_REASON}."
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES and runtime.get("productOwnerExecutable") is False:
        return str(
            redact_secrets(
                runtime.get("reason")
                or "CLI runtime has not passed the ProductOwnerAgent-specific safety contract."
            )
        )
    if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES and (
        "chat" not in capabilities or runtime.get("canRunPrompt") is False
    ):
        return "CLI runtime does not advertise the chat prompt capability required by ProductOwnerAgent."
    if is_ollama_runtime(runtime) and not runtime.get("models"):
        return "Ollama is reachable but no model is available for ProductOwnerAgent execution."
    return str(redact_secrets(runtime.get("reason") or "Runtime is not executable for ProductOwnerAgent."))


def is_product_owner_runtime(runtime: dict[str, Any]) -> bool:
    """Indica si un runtime sirve como ProductOwnerAgent: CLI real ejecutable o modelo con chat disponible.

    Un runtime de modelo local verificado (llama.cpp, LM Studio, vLLM u Ollama) exige además modelos listados,
    igual que Ollama: sin modelo no hay a quién pedirle el brief.
    """
    runtime_id = str(runtime.get("id") or "")
    runtime_family = runtime_provider_family(runtime)
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
    if runtime.get("localModelRuntime") is True or is_ollama_runtime(runtime):
        return bool("chat" in capabilities and runtime.get("models"))
    if runtime_family in PRODUCT_OWNER_AGENT_REMOTE_API_RUNTIMES:
        return "chat" in capabilities
    return False


def _runtime_resolution(
    runtime_statuses: list[dict[str, Any]], preferred_runtime: str | None
) -> dict[str, Any]:
    """Project only non-secret evidence; readiness is neither authorization nor an observation of a run."""
    from .codex_compatibility import contract_fingerprint

    candidates = []
    for runtime in runtime_statuses:
        runtime_id = str(runtime.get("id") or "")
        if (
            runtime_id not in PRODUCT_OWNER_AGENT_CLI_RUNTIMES
            and runtime_provider_family(runtime) not in PRODUCT_OWNER_AGENT_MODEL_RUNTIMES
        ):
            continue
        compatibility = runtime.get("compatibility") or {}
        eligible = is_product_owner_runtime(runtime)
        candidates.append(
            {
                "runtimeId": runtime_id,
                "eligible": eligible,
                "detectedCommand": runtime.get("detectedCommand"),
                "executableSource": runtime.get("executableSource"),
                "version": runtime.get("version"),
                "versionVerified": runtime.get("versionVerified"),
                "binaryFingerprint": compatibility.get("binaryFingerprint"),
                "contractFingerprint": contract_fingerprint() if runtime_id == "codex_cli" else None,
                "capabilityVersion": compatibility.get("version"),
                "compatibilityStatus": compatibility.get("status"),
                "evidenceCheckedAt": compatibility.get("lastCheckedAt"),
                "configured": runtime.get("configured"),
                "authenticated": runtime.get("authenticated"),
                "healthy": runtime.get("healthy"),
                "policyAllowed": runtime.get("policyAllowed"),
                "resourceAdmissible": runtime.get("resourceAdmissible"),
                "reason": runtime.get("reason") if eligible else _product_owner_runtime_reason(runtime),
            }
        )
    return redact_secrets(
        {
            "requestedRuntime": preferred_runtime,
            "source": "request.preferredRuntime" if preferred_runtime else "eligible_cost_order",
            "candidates": candidates,
            "permissions": {
                "permissionProfile": "plan",
                "allowedTools": PRODUCT_OWNER_AGENT_ALLOWED_TOOLS,
                "executionAuthorized": False,
                "authority": "ToolBroker",
            },
            # No provider call has occurred. Local policy (including no_limit) is not provider quota.
            "observedEffort": None,
            "remainingQuota": None,
        }
    )


def product_owner_agent_readiness(
    runtime_statuses: list[dict[str, Any]],
    *,
    preferred_runtime: str | None = None,
) -> dict[str, Any]:
    """Selecciona entre elegibles; una preferencia explícita no elegible falla sin fallback.

    Prioriza cuentas free-tier declaradas por el operador y modelos locales; los demás runtimes quedan
    disponibles como alternativas sujetas a la política de costo y aprobación.

    Returns:
        Estado de readiness con executable/status/reason, el runtime elegido, los candidatos y el contrato.
    """
    resolution = _runtime_resolution(runtime_statuses, preferred_runtime)
    by_id = {str(runtime.get("id")): runtime for runtime in runtime_statuses}
    eligible = [runtime for runtime in runtime_statuses if is_product_owner_runtime(runtime)]
    ordered_eligible = sorted(
        eligible,
        key=lambda item: (
            _product_owner_runtime_cost_rank(item),
            PRODUCT_OWNER_AGENT_RUNTIME_ORDER.index(_product_owner_runtime_order_id(item))
            if _product_owner_runtime_order_id(item) in PRODUCT_OWNER_AGENT_RUNTIME_ORDER
            else len(PRODUCT_OWNER_AGENT_RUNTIME_ORDER),
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
                "reason": redact_secrets(f"Runtime provider is not catalogued: {preferred_runtime}"),
                "selectedRuntimeId": None,
                "candidateRuntimeIds": [str(runtime["id"]) for runtime in ordered_eligible],
                "contract": product_owner_agent_contract(),
                "resolution": resolution,
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
                "resolution": resolution,
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
            "resolution": resolution,
        }
    return {
        "id": PRODUCT_OWNER_AGENT_ID,
        "executable": False,
        "status": "runtime_unavailable",
        "reason": "No executable ProductOwnerAgent runtime is configured.",
        "selectedRuntimeId": None,
        "candidateRuntimeIds": [],
        "contract": product_owner_agent_contract(),
        "resolution": resolution,
    }
