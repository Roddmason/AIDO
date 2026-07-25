"""Canonical provider setup catalog for known API, gateway, and Ollama providers.

The catalog owns provider presets used by setup APIs: default endpoints, required
operator inputs, credential mode, model discovery strategy, docs/pricing references,
and representative model ids. Provider accounts remain the mutable runtime state; this
module is the immutable product contract for creating those accounts safely.

@author Rodrigo Mason
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .model_gateway_models import ApiFamily, DeploymentMode, PricingMode, TermsMode


@dataclass(frozen=True)
class ProviderCatalogEntry:
    """One known provider preset exposed by the backend catalog API."""

    id: str
    display_name: str
    provider_type: str
    api_format: str
    default_base_url: str | None
    required_fields: tuple[str, ...]
    credential_kind: str
    known_models: tuple[str, ...]
    model_sync: dict[str, Any]
    capabilities: tuple[str, ...]
    docs_url: str
    pricing_source: str
    provider_family: str
    deployment_mode: DeploymentMode = "custom"
    api_family: ApiFamily = "chat_completions"
    adapter_profile: str = "auto"
    terms_mode: TermsMode = "unspecified"
    pricing_mode: PricingMode = "unknown"
    aliases: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        """Serialize with the camelCase contract used by HTTP clients."""
        data = asdict(self)
        return {
            "id": data["id"],
            "displayName": data["display_name"],
            "providerType": data["provider_type"],
            "apiFormat": data["api_format"],
            "defaultBaseUrl": data["default_base_url"],
            "requiredFields": list(data["required_fields"]),
            "credentialKind": data["credential_kind"],
            "knownModels": list(data["known_models"]),
            "modelSync": data["model_sync"],
            "capabilities": list(data["capabilities"]),
            "docsUrl": data["docs_url"],
            "pricingSource": data["pricing_source"],
            "providerFamily": data["provider_family"],
            "deploymentMode": data["deployment_mode"],
            "apiFamily": data["api_family"],
            "adapterProfile": data["adapter_profile"],
            "termsMode": data["terms_mode"],
            "pricingMode": data["pricing_mode"],
            "aliases": list(data["aliases"]),
        }


PROVIDER_CATALOG_VERSION = "2026-07-25"
OPENAI_COMPATIBLE_SYNC = {"strategy": "api_list_models", "endpoint": "/models"}

# Gemini exposes model ids through the OpenAI-compatible ``/models`` endpoint, but that
# response intentionally does not carry the capability, context, or pricing metadata
# required by AIDO's router.  Keep the official, reviewed metadata in one manifest and
# overlay it only for exact stable model ids.  ``freeTier`` means "Google offers this
# model on a free project"; the provider account's ``pricingMode`` still decides whether
# a particular call is free.
GEMINI_MODEL_MANIFEST: dict[str, dict[str, Any]] = {
    "gemini-3.5-flash": {
        "displayName": "Gemini 3.5 Flash",
        "modelFamily": "gemini-3.5",
        "contextWindow": 1_048_576,
        "maxOutputTokens": 65_536,
        "supportsTools": True,
        "supportsJson": True,
        "supportsStreaming": True,
        "supportsVision": True,
        "supportsReasoning": True,
        "supportsThinking": True,
        "inputPricePerMtok": 1.50,
        "cachedInputPricePerMtok": 0.15,
        "outputPricePerMtok": 9.00,
        "reasoningPricePerMtok": 9.00,
        "freeTier": True,
        "freeTierNotes": (
            "Available on Google AI Studio free-tier projects subject to dynamic per-model "
            "RPM/TPM/RPD limits. 1,048,576 is the per-request input context limit, not a quota."
        ),
    },
    "gemini-3.1-flash-lite": {
        "displayName": "Gemini 3.1 Flash-Lite",
        "modelFamily": "gemini-3.1",
        "contextWindow": 1_048_576,
        "maxOutputTokens": 65_536,
        "supportsTools": True,
        "supportsJson": True,
        "supportsStreaming": True,
        "supportsVision": True,
        "supportsReasoning": True,
        "supportsThinking": True,
        "inputPricePerMtok": 0.25,
        "cachedInputPricePerMtok": 0.025,
        "outputPricePerMtok": 1.50,
        "reasoningPricePerMtok": 1.50,
        "freeTier": True,
        "freeTierNotes": "Stable high-volume free-tier fallback; dynamic project/model limits apply.",
    },
    "gemini-2.5-flash": {
        "displayName": "Gemini 2.5 Flash",
        "modelFamily": "gemini-2.5",
        "contextWindow": 1_048_576,
        "maxOutputTokens": 65_536,
        "supportsTools": True,
        "supportsJson": True,
        "supportsStreaming": True,
        "supportsVision": True,
        "supportsReasoning": True,
        "supportsThinking": True,
        "inputPricePerMtok": 0.30,
        "cachedInputPricePerMtok": 0.03,
        "outputPricePerMtok": 2.50,
        "reasoningPricePerMtok": 2.50,
        "freeTier": True,
        "freeTierNotes": "Available on free-tier projects; dynamic project/model limits apply.",
    },
    "gemini-2.5-flash-lite": {
        "displayName": "Gemini 2.5 Flash-Lite",
        "modelFamily": "gemini-2.5",
        "contextWindow": 1_048_576,
        "maxOutputTokens": 65_536,
        "supportsTools": True,
        "supportsJson": True,
        "supportsStreaming": True,
        "supportsVision": True,
        "supportsReasoning": True,
        "supportsThinking": True,
        "inputPricePerMtok": 0.10,
        "cachedInputPricePerMtok": 0.01,
        "outputPricePerMtok": 0.40,
        "reasoningPricePerMtok": 0.40,
        "freeTier": True,
        "freeTierNotes": "Available on free-tier projects; dynamic project/model limits apply.",
    },
    "gemini-2.5-pro": {
        "displayName": "Gemini 2.5 Pro",
        "modelFamily": "gemini-2.5",
        "contextWindow": 1_048_576,
        "maxOutputTokens": 65_536,
        "supportsTools": True,
        "supportsJson": True,
        "supportsStreaming": True,
        "supportsVision": True,
        "supportsReasoning": True,
        "supportsThinking": True,
        # Paid prices are prompt-length tiered and the current catalog schema cannot
        # represent that boundary honestly. An operator-attested free account is still cost zero.
        "freeTier": True,
        "freeTierNotes": (
            "Available on free-tier projects. Paid pricing is prompt-length tiered and must be "
            "configured explicitly before paid routing."
        ),
    },
}

PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        id="openai_api",
        display_name="OpenAI",
        provider_type="api",
        api_format="responses",
        default_base_url="https://api.openai.com/v1",
        required_fields=("credentialRef",),
        credential_kind="api_key",
        known_models=("gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
        model_sync={"strategy": "api_list_models", "endpoint": "/models"},
        capabilities=("chat", "responses", "tools", "json", "vision", "reasoning", "streaming"),
        docs_url="https://developers.openai.com/api/reference/overview/",
        pricing_source="https://developers.openai.com/api/docs/pricing",
        provider_family="openai_api",
        aliases=("openai",),
    ),
    ProviderCatalogEntry(
        id="anthropic_api",
        display_name="Anthropic",
        provider_type="api",
        api_format="anthropic",
        default_base_url="https://api.anthropic.com/v1",
        required_fields=("credentialRef",),
        credential_kind="api_key",
        known_models=("claude-fable-5", "claude-sonnet-5", "claude-opus-4-8"),
        model_sync={"strategy": "api_list_models", "endpoint": "/models"},
        capabilities=("chat", "messages", "tools", "json", "vision", "reasoning", "streaming"),
        docs_url="https://docs.anthropic.com/en/api/messages",
        pricing_source="https://docs.anthropic.com/en/docs/about-claude/pricing",
        provider_family="anthropic_api",
        aliases=("anthropic", "claude"),
    ),
    ProviderCatalogEntry(
        id="openrouter",
        display_name="OpenRouter",
        provider_type="gateway",
        api_format="openai_compatible",
        default_base_url="https://openrouter.ai/api/v1",
        required_fields=("credentialRef",),
        credential_kind="bearer_token",
        known_models=("openrouter/free",),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "routing", "tools", "json", "vision", "streaming"),
        docs_url="https://openrouter.ai/docs/api/reference/overview",
        pricing_source="https://openrouter.ai/docs/guides/overview/models",
        provider_family="openrouter",
    ),
    ProviderCatalogEntry(
        id="litellm",
        display_name="LiteLLM Proxy",
        provider_type="gateway",
        api_format="openai_compatible",
        default_base_url=None,
        required_fields=("baseUrl",),
        credential_kind="optional_bearer_token",
        known_models=(),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "routing", "tools", "json", "streaming"),
        docs_url="https://docs.litellm.ai/docs/",
        pricing_source="operator_managed_gateway",
        provider_family="litellm",
    ),
    ProviderCatalogEntry(
        id="omniroute",
        display_name="OmniRoute",
        provider_type="gateway",
        api_format="openai_compatible",
        default_base_url="http://localhost:20128/v1",
        required_fields=(),
        credential_kind="optional_bearer_token",
        known_models=(),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        # La familia queda en openai_compatible a propósito: es la única familia gateway con tool
        # adapter en el ToolBroker y presente en MODEL_RUNTIME_TOOLS del policy engine, así la
        # cuenta ejecuta en el loop sin registrar adapters nuevos.
        capabilities=("chat", "routing", "tools_optional", "json_optional", "streaming_optional"),
        docs_url="https://github.com/diegosouzapw/OmniRoute",
        pricing_source="operator_managed_gateway",
        provider_family="openai_compatible",
        # self_hosted: el gateway corre en el equipo del operador y su auth es la del host, así que
        # el bearer token es opcional (mismo criterio que LiteLLM Proxy y Ollama).
        deployment_mode="self_hosted_development",
    ),
    ProviderCatalogEntry(
        id="nvidia_nim",
        display_name="NVIDIA NIM",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://integrate.api.nvidia.com/v1",
        required_fields=("credentialRef",),
        credential_kind="bearer_token",
        known_models=("auto_best_available",),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools", "json", "vision", "streaming"),
        docs_url="https://docs.api.nvidia.com/nim/reference/llm-apis",
        pricing_source="https://build.nvidia.com/pricing",
        provider_family="nvidia_nim",
        deployment_mode="hosted_trial",
        api_family="chat_completions",
        terms_mode="evaluation",
        pricing_mode="unknown",
        aliases=("nim",),
    ),
    ProviderCatalogEntry(
        id="deepseek",
        display_name="DeepSeek",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://api.deepseek.com",
        required_fields=("credentialRef",),
        credential_kind="bearer_token",
        known_models=("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools", "json", "reasoning", "streaming"),
        docs_url="https://api-docs.deepseek.com/",
        pricing_source="https://api-docs.deepseek.com/quick_start/pricing",
        provider_family="deepseek",
    ),
    ProviderCatalogEntry(
        id="kimi",
        display_name="Moonshot/Kimi",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://api.moonshot.ai/v1",
        required_fields=("credentialRef",),
        credential_kind="bearer_token",
        known_models=("kimi-k2.7-code-highspeed", "kimi-k2.7-code", "kimi-k2.6"),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools", "json", "vision", "reasoning", "streaming"),
        docs_url="https://platform.kimi.ai/docs/overview",
        pricing_source="https://platform.kimi.ai/",
        provider_family="kimi",
        aliases=("moonshot", "moonshot_kimi"),
    ),
    ProviderCatalogEntry(
        id="mistral",
        display_name="Mistral",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://api.mistral.ai/v1",
        required_fields=("credentialRef",),
        credential_kind="bearer_token",
        known_models=("mistral-large-latest", "mistral-small-latest", "magistral-medium-latest"),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools", "json", "vision", "reasoning", "streaming"),
        docs_url="https://docs.mistral.ai/api/",
        pricing_source="https://mistral.ai/pricing/api/",
        provider_family="mistral",
    ),
    ProviderCatalogEntry(
        id="groq",
        display_name="Groq",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://api.groq.com/openai/v1",
        required_fields=("credentialRef",),
        credential_kind="bearer_token",
        known_models=("openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "responses", "tools", "json", "fast", "streaming"),
        docs_url="https://console.groq.com/docs/openai",
        pricing_source="https://console.groq.com/docs/models",
        provider_family="groq",
    ),
    ProviderCatalogEntry(
        id="gemini",
        display_name="Google Gemini",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        required_fields=("credentialRef",),
        credential_kind="api_key",
        known_models=tuple(GEMINI_MODEL_MANIFEST),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools", "json", "vision", "reasoning", "streaming"),
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
        pricing_source="https://ai.google.dev/gemini-api/docs/pricing",
        provider_family="gemini",
        aliases=("google_gemini", "google"),
    ),
    ProviderCatalogEntry(
        id="azure_openai",
        display_name="Azure OpenAI",
        provider_type="api",
        api_format="azure_openai",
        default_base_url=None,
        required_fields=("baseUrl", "credentialRef"),
        credential_kind="api_key_header",
        known_models=(),
        model_sync={"strategy": "api_list_models", "endpoint": "/models", "deploymentScoped": True},
        capabilities=("chat", "responses", "tools", "json", "vision", "enterprise", "streaming"),
        docs_url="https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/chat",
        pricing_source="https://azure.microsoft.com/en-us/pricing/details/azure-openai/",
        provider_family="azure_openai",
        aliases=("azure",),
    ),
    ProviderCatalogEntry(
        id="ollama",
        display_name="Ollama local",
        provider_type="local",
        api_format="ollama",
        default_base_url="http://localhost:11434",
        required_fields=(),
        credential_kind="none",
        known_models=(),
        model_sync={"strategy": "ollama_tags", "endpoint": "/api/tags"},
        capabilities=("chat", "local", "private", "streaming"),
        docs_url="https://docs.ollama.com/api/introduction",
        pricing_source="local_runtime_cost_only",
        provider_family="ollama",
        aliases=("ollama_local",),
    ),
    ProviderCatalogEntry(
        id="ollama_remote",
        display_name="Ollama remote",
        provider_type="local",
        api_format="ollama",
        default_base_url=None,
        required_fields=("baseUrl",),
        credential_kind="optional_bearer_token",
        known_models=(),
        model_sync={"strategy": "ollama_tags", "endpoint": "/api/tags"},
        capabilities=("chat", "self_hosted", "private_optional", "streaming"),
        docs_url="https://docs.ollama.com/api/tags",
        pricing_source="operator_managed_remote_runtime",
        provider_family="ollama",
        aliases=("remote_ollama",),
    ),
    ProviderCatalogEntry(
        id="openai_compatible",
        display_name="Custom OpenAI-compatible",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url=None,
        required_fields=("baseUrl", "credentialRef"),
        credential_kind="bearer_token",
        known_models=(),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools_optional", "json_optional", "streaming_optional"),
        docs_url="https://developers.openai.com/api/reference/overview/",
        pricing_source="operator_managed",
        provider_family="openai_compatible",
        aliases=("custom", "custom_openai_compatible"),
    ),
)

REMOTE_MODEL_PROVIDER_FAMILIES = frozenset(
    entry.provider_family for entry in PROVIDER_CATALOG if entry.provider_type in {"api", "gateway"}
)
MODEL_PROVIDER_FAMILIES = REMOTE_MODEL_PROVIDER_FAMILIES | frozenset(
    entry.provider_family for entry in PROVIDER_CATALOG if entry.provider_type == "local"
)

_CATALOG_BY_ID = {entry.id: entry for entry in PROVIDER_CATALOG}
_ALIASES = {alias: entry.id for entry in PROVIDER_CATALOG for alias in entry.aliases}


def list_provider_catalog() -> list[dict[str, Any]]:
    """Return the public provider catalog in stable display order."""
    return [entry.public_dict() for entry in PROVIDER_CATALOG]


def canonical_provider_id(provider_id: str) -> str:
    """Resolve known aliases to the canonical provider account id."""
    normalized = str(provider_id or "").strip()
    return _ALIASES.get(normalized, normalized)


def provider_catalog_entry(provider_id: str) -> ProviderCatalogEntry | None:
    """Return a catalog entry by canonical id or alias."""
    return _CATALOG_BY_ID.get(canonical_provider_id(provider_id))


def enrich_catalog_model(
    entry: ProviderCatalogEntry,
    discovered: dict[str, Any],
) -> dict[str, Any]:
    """Overlay reviewed provider metadata onto one discovered model record.

    Unknown or preview Gemini model ids remain discoverable but intentionally retain
    unknown pricing/capabilities until their metadata is reviewed.
    """
    model_id = str(discovered.get("model") or "").strip()
    if entry.provider_family != "gemini" or model_id not in GEMINI_MODEL_MANIFEST:
        return dict(discovered)
    return {
        **discovered,
        **GEMINI_MODEL_MANIFEST[model_id],
        "source": f"official_manifest:{PROVIDER_CATALOG_VERSION}",
    }
