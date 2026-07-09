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
            "aliases": list(data["aliases"]),
        }


PROVIDER_CATALOG_VERSION = "2026-07-09"
OPENAI_COMPATIBLE_SYNC = {"strategy": "api_list_models", "endpoint": "/models"}

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
    ),
    ProviderCatalogEntry(
        id="gemini",
        display_name="Google Gemini",
        provider_type="api",
        api_format="openai_compatible",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        required_fields=("credentialRef",),
        credential_kind="api_key",
        known_models=("gemini-3.5-flash",),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "tools", "json", "vision", "reasoning", "streaming"),
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
        pricing_source="https://ai.google.dev/gemini-api/docs/pricing",
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
        aliases=("custom", "custom_openai_compatible"),
    ),
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
