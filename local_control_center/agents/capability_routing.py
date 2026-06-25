"""Routing por capacidad: unifica los runtimes/providers tras un registro de capacidades, no por nombre.

En vez de elegir un provider por su nombre, el caller declara las CAPACIDADES que la tarea necesita
(reasoning, code_edit, vision, large_context, local_private, low_cost, …) y este módulo —lógica pura y
determinista, sin DB ni I/O— devuelve el provider de modelo más apropiado que las cubre, los candidatos
de respaldo (para escalar si el elegido falla) y el bus de tools (MCP) a componer cuando hace falta
``web_research``. No ejecuta todos los providers: selecciona un subconjunto.

Distingue providers de MODELO (ejecutan un modelo) del bus de TOOLS (``mcp_tools``: surface de tools que
un modelo invoca, no un ejecutor). ``web_research`` la aporta el bus de tools, así que una tarea de
investigación se resuelve componiendo un modelo con razonamiento + ``mcp_tools``. El registro es la fuente
canónica de capacidades por provider y se alinea con las restricciones de runtime del ``model_router``
(p. ej. ``code_edit`` solo en runtimes CLI); el ``model_router`` (DB-backed) sigue siendo el árbitro final
del modelo concreto, su salud y el presupuesto.
"""

from __future__ import annotations

CAPABILITIES = frozenset(
    {
        "reasoning",
        "product_analysis",
        "web_research",
        "code_edit",
        "code_review",
        "tool_use",
        "vision",
        "large_context",
        "local_private",
        "low_cost",
        "structured_output",
    }
)

# Capabilities only a tool bus can provide; a model alone never satisfies them.
TOOL_ONLY_CAPABILITIES = frozenset({"web_research"})

# Provider kind, so callers know how to execute the choice (CLI agent / local model / remote API / tools).
PROVIDER_KINDS: dict[str, str] = {
    "codex_cli": "cli",
    "claude_code_cli": "cli",
    "openhands": "cli",
    "swe_agent": "cli",
    "ollama_local": "local",
    "ollama_remote": "remote_api",
    "openai_compatible": "remote_api",
    "anthropic_api": "remote_api",
    "openrouter": "remote_api",
    "nvidia_nim": "remote_api",
    "mcp_tools": "tools",
}

# Canonical capability matrix. Model-provider capabilities are STRUCTURAL (e.g. code_edit only on CLI
# runtimes, aligned with model_router); concrete model capabilities (which model has vision/tools) are the
# model_router's job downstream. Aspirational capabilities are deliberately omitted (openhands/swe_agent do
# not guarantee structured JSON; model providers do not themselves browse the web — web_research is a tool).
PROVIDER_CAPABILITIES: dict[str, frozenset[str]] = {
    "codex_cli": frozenset({"reasoning", "code_edit", "code_review", "tool_use", "structured_output"}),
    "claude_code_cli": frozenset(
        {"reasoning", "code_edit", "code_review", "tool_use", "large_context", "structured_output"}
    ),
    "openhands": frozenset({"code_edit", "tool_use"}),
    "swe_agent": frozenset({"code_edit", "code_review"}),
    "ollama_local": frozenset({"reasoning", "local_private", "low_cost", "structured_output"}),
    "ollama_remote": frozenset({"reasoning", "low_cost", "structured_output"}),
    "openai_compatible": frozenset(
        {"reasoning", "product_analysis", "code_review", "tool_use", "vision", "structured_output"}
    ),
    "anthropic_api": frozenset(
        {
            "reasoning",
            "product_analysis",
            "code_review",
            "tool_use",
            "vision",
            "large_context",
            "structured_output",
        }
    ),
    "openrouter": frozenset(
        {
            "reasoning",
            "product_analysis",
            "code_review",
            "tool_use",
            "vision",
            "large_context",
            "structured_output",
        }
    ),
    # nvidia_nim is a REMOTE api (apiKey + baseUrl); it is NOT local_private despite being self-hostable.
    "nvidia_nim": frozenset({"reasoning", "structured_output"}),
    # mcp_tools is the tool bus, not a model executor — it only supplies tool capabilities.
    "mcp_tools": frozenset({"tool_use", "web_research"}),
}

MODEL_PROVIDERS = tuple(name for name, kind in PROVIDER_KINDS.items() if kind != "tools")
TOOL_PROVIDERS = tuple(name for name, kind in PROVIDER_KINDS.items() if kind == "tools")

# Default quality/capability preference among model providers (earlier = preferred).
PROVIDER_RANK = (
    "claude_code_cli",
    "anthropic_api",
    "codex_cli",
    "openrouter",
    "openai_compatible",
    "nvidia_nim",
    "openhands",
    "swe_agent",
    "ollama_remote",
    "ollama_local",
)
_RANK_INDEX = {provider: index for index, provider in enumerate(PROVIDER_RANK)}

# Only ollama_local runs the model on the operator's machine (truly private); ollama_remote is a cheap
# remote. nvidia_nim is excluded from local-private on purpose (see matrix note).
LOCAL_EXEC_PROVIDERS = frozenset({"ollama_local"})
LOW_COST_PROVIDERS = frozenset({"ollama_local", "ollama_remote"})


class CapabilityRouteError(ValueError):
    """Se lanza cuando se piden capacidades fuera del catálogo soportado."""


def capabilities_of(provider: str) -> frozenset[str]:
    """Devuelve el conjunto de capacidades que declara ``provider``.

    Raises:
        CapabilityRouteError: si el provider no está en el registro.
    """
    if provider not in PROVIDER_CAPABILITIES:
        raise CapabilityRouteError(f"Unknown provider: {provider}.")
    return PROVIDER_CAPABILITIES[provider]


def providers_for(capability: str) -> list[str]:
    """Lista los providers (orden de preferencia) que soportan ``capability``.

    Raises:
        CapabilityRouteError: si la capacidad no pertenece al catálogo.
    """
    if capability not in CAPABILITIES:
        raise CapabilityRouteError(f"Unknown capability: {capability}.")
    matches = [p for p, caps in PROVIDER_CAPABILITIES.items() if capability in caps]
    return sorted(matches, key=lambda p: _RANK_INDEX.get(p, len(PROVIDER_RANK)))


def _validate(required: frozenset[str]) -> None:
    unknown = required - CAPABILITIES
    if unknown:
        raise CapabilityRouteError(f"Unknown capabilities: {sorted(unknown)}.")


def can_cover(required: set[str] | frozenset[str]) -> bool:
    """Indica si un solo provider de modelo (más ``mcp_tools`` para ``web_research``) cubre lo requerido."""
    required = frozenset(required)
    _validate(required)
    return _route(required, prefer_local=False, prefer_low_cost=False, exclude=frozenset())["covered"]


def _rank_key(provider: str, *, prefer_local: bool, prefer_low_cost: bool) -> tuple[int, int, int]:
    local_first = 0 if (prefer_local and provider in LOCAL_EXEC_PROVIDERS) else 1
    cheap_first = 0 if (prefer_low_cost and provider in LOW_COST_PROVIDERS) else 1
    return (local_first, cheap_first, _RANK_INDEX.get(provider, len(PROVIDER_RANK)))


def _route(
    required: frozenset[str], *, prefer_local: bool, prefer_low_cost: bool, exclude: frozenset[str]
) -> dict:
    prefer_local = prefer_local or "local_private" in required
    prefer_low_cost = prefer_low_cost or "low_cost" in required

    needs_web = "web_research" in required
    # web_research is a tool capability; the model must support tool_use to invoke the web tool.
    model_required = (required - TOOL_ONLY_CAPABILITIES) | ({"tool_use"} if needs_web else set())

    qualifiers = [
        provider
        for provider in MODEL_PROVIDERS
        if provider not in exclude and model_required <= PROVIDER_CAPABILITIES[provider]
    ]
    qualifiers.sort(key=lambda p: _rank_key(p, prefer_local=prefer_local, prefer_low_cost=prefer_low_cost))

    tools = ["mcp_tools"] if needs_web and "mcp_tools" not in exclude else []
    selected = qualifiers[0] if qualifiers else None
    covered = selected is not None and (not needs_web or bool(tools))

    rejected = [
        {"provider": provider, "missing": sorted(model_required - PROVIDER_CAPABILITIES[provider])}
        for provider in MODEL_PROVIDERS
        if provider not in exclude and not (model_required <= PROVIDER_CAPABILITIES[provider])
    ]
    return {
        "selected": selected,
        "candidates": qualifiers,
        "tools": tools,
        "rejected": rejected,
        "covered": covered,
        "modelRequired": sorted(model_required),
    }


def route(
    required: set[str] | frozenset[str],
    *,
    prefer_local: bool = False,
    prefer_low_cost: bool = False,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """Selecciona el provider de modelo que cubre las capacidades requeridas y el bus de tools a componer.

    No ejecuta todos los providers: devuelve un único ``selected`` (el mejor que cubre lo requerido),
    ``candidates`` ordenados para escalar si el elegido falla, y ``tools`` (``mcp_tools``) cuando la tarea
    pide ``web_research``. ``prefer_local``/``prefer_low_cost`` priorizan ejecución local o barata; pedir
    ``local_private``/``low_cost`` como capacidad ya fuerza esa preferencia. ``selected`` es ``None`` y
    ``covered`` es ``False`` cuando ningún provider de modelo cubre lo requerido (ver ``rejected`` para lo
    que falta a cada uno).

    Raises:
        CapabilityRouteError: si se pide una capacidad fuera del catálogo.
    """
    required = frozenset(required)
    _validate(required)
    result = _route(
        required,
        prefer_local=prefer_local,
        prefer_low_cost=prefer_low_cost,
        exclude=frozenset(exclude),
    )
    return {"required": sorted(required), **result}
