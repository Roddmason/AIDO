"""Resolve persisted provider accounts to their concrete model adapter.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_provider_config import (
    known_provider_default_base_url,
    runtime_provider_configuration_for_account,
)

from .anthropic_api import AnthropicAPIProvider
from .azure_openai import AzureOpenAIProvider
from .capabilities import HttpTransport, ImageArtifactStore
from .gemini import GeminiProvider
from .litellm_adapter import LiteLLMAdapter
from .nvidia_nim import NvidiaNimProvider, NvidiaNimVisualProvider
from .ollama import OllamaProvider
from .openai_api import OpenAIAPIProvider
from .openai_compatible import OpenAICompatibleProvider
from .openrouter import OpenRouterProvider


class ProviderAdapterResolutionError(RuntimeError):
    """Base error for stable, pre-network provider resolution failures."""

    code = "provider_adapter_resolution_failed"


class ProviderAccountDisabledError(ProviderAdapterResolutionError):
    """Raised when execution tries to resolve a disabled persisted account."""

    code = "provider_account_disabled"

    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        super().__init__(f"{self.code}:{provider_id}")


class UnsupportedProviderCapabilityError(ProviderAdapterResolutionError):
    """Raised when an account's explicit API family has no adapter yet."""

    code = "provider_api_family_unsupported"
    public_code = "unsupported_api_family"

    def __init__(self, *, provider_id: str, provider_family: str, api_family: str):
        self.provider_id = provider_id
        self.provider_family = provider_family
        self.api_family = api_family
        super().__init__(f"{self.code}:{provider_family}:{api_family}:{provider_id}")


class AdapterProfileRequiredError(ProviderAdapterResolutionError):
    """Raised when an ambiguous capability cannot safely use adapterProfile=auto."""

    code = "adapter_profile_required"

    def __init__(self, *, provider_id: str, api_family: str):
        self.provider_id = provider_id
        self.api_family = api_family
        super().__init__(f"{self.code}:{api_family}:{provider_id}")


class ProviderBaseUrlRequiredError(ProviderAdapterResolutionError):
    """Raised when a contract-specific endpoint root was not configured."""

    code = "provider_base_url_required"

    def __init__(self, *, provider_id: str, api_family: str):
        self.provider_id = provider_id
        self.api_family = api_family
        super().__init__(f"{self.code}:{api_family}:{provider_id}")


class ProviderConfigurationConflictError(ProviderAdapterResolutionError):
    """Raised when aliases provide conflicting credentials for one canonical provider."""

    code = "provider_configuration_conflict"
    public_code = "provider_configuration_conflict"

    def __init__(self, *, provider_id: str):
        self.provider_id = provider_id
        super().__init__(f"{self.code}:{provider_id}")


class UnsupportedAdapterProfileError(ProviderAdapterResolutionError):
    """Raised when a persisted adapter profile has no documented implementation."""

    code = "unsupported_adapter_profile"

    def __init__(self, *, provider_id: str, api_family: str, adapter_profile: str):
        self.provider_id = provider_id
        self.api_family = api_family
        self.adapter_profile = adapter_profile
        super().__init__(f"{self.code}:{api_family}:{adapter_profile}:{provider_id}")


def provider_account_requires_credential(account: dict[str, Any]) -> bool:
    """Return whether this endpoint contract requires an auth credential to execute."""
    provider_family = str(account.get("providerFamily") or "").strip()
    api_format = str(account.get("apiFormat") or "").strip()
    deployment_mode = str(account.get("deploymentMode") or "").strip()
    if provider_family in {"ollama", "local_ollama", "litellm"} or api_format == "ollama":
        return False
    if provider_family == "nvidia_nim":
        return not deployment_mode.startswith("self_hosted")
    return str(account.get("providerType") or "").strip() in {"api", "gateway"}


def provider_account_policy_kind(account: dict[str, Any]) -> str:
    """Return a policy kind that cannot be weakened by client-controlled account metadata."""
    if str(account.get("providerFamily") or "").strip() == "nvidia_nim":
        return "api"
    return str(account.get("providerType") or "api").strip() or "api"


def provider_account_requires_explicit_model_manifest(account: dict[str, Any]) -> bool:
    """Return whether NVIDIA documents no model-list route for this endpoint contract."""
    if str(account.get("providerFamily") or "").strip() != "nvidia_nim":
        return False
    api_family = str(account.get("apiFamily") or "").strip()
    deployment_mode = str(account.get("deploymentMode") or "").strip()
    if api_family in {"image_generation", "image_editing"}:
        return True
    return api_family == "rerank" and not deployment_mode.startswith("self_hosted")


class ProviderAdapterFactory:
    """Build one model adapter from persisted endpoint identity and explicit family metadata."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        transport: HttpTransport | None = None,
        image_artifact_store: ImageArtifactStore | None = None,
    ):
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)
        self.transport = transport
        self.image_artifact_store = image_artifact_store

    def resolve(self, provider_id: str) -> Any:
        """Build an adapter for persisted configuration without granting execution authority."""
        account = self.accounts.get_provider_account(provider_id)
        provider_family = str(account.get("providerFamily") or "").strip()
        api_family = str(account.get("apiFamily") or "").strip()
        adapter_profile = str(account.get("adapterProfile") or "auto").strip()
        if provider_family == "nvidia_nim":
            deployment_mode = str(account.get("deploymentMode") or "custom").strip()
            if api_family in {"image_generation", "image_editing"} and adapter_profile == "auto":
                raise AdapterProfileRequiredError(
                    provider_id=provider_id,
                    api_family=api_family,
                )
            if api_family not in {
                "chat_completions",
                "embeddings",
                "rerank",
                "image_generation",
                "image_editing",
            }:
                raise UnsupportedProviderCapabilityError(
                    provider_id=provider_id,
                    provider_family=provider_family,
                    api_family=api_family or "unspecified",
                )
            allowed_profiles = {
                "chat_completions": {"auto", "nvidia_openai_chat"},
                "embeddings": {"auto", "nvidia_openai_embeddings"},
                "rerank": (
                    {"auto", "nvidia_nim_ranking"}
                    if deployment_mode.startswith("self_hosted")
                    else {"auto", "nvidia_hosted_rerank"}
                ),
                "image_generation": (
                    {"nvidia_qwen_image_generation_infer", "nvidia_openai_image_generation"}
                    if deployment_mode.startswith("self_hosted")
                    else {"nvidia_hosted_prompt_image_generation"}
                    if deployment_mode in {"hosted_trial", "partner_paid"}
                    else set()
                ),
                "image_editing": (
                    {"nvidia_qwen_image_editing_infer", "nvidia_openai_image_editing"}
                    if deployment_mode.startswith("self_hosted")
                    else set()
                ),
            }
            if adapter_profile not in allowed_profiles[api_family]:
                raise UnsupportedAdapterProfileError(
                    provider_id=provider_id,
                    api_family=api_family,
                    adapter_profile=adapter_profile,
                )

        base_url, credential_ref = self._connection_configuration(account)
        api_format = str(account.get("apiFormat") or "").strip()
        if provider_family == "nvidia_nim":
            if not base_url:
                raise ProviderBaseUrlRequiredError(
                    provider_id=provider_id,
                    api_family=api_family or "unspecified",
                )
            provider_class = (
                NvidiaNimVisualProvider
                if api_family in {"image_generation", "image_editing"}
                else NvidiaNimProvider
            )
            visual_options = (
                {"image_artifact_store": self.image_artifact_store}
                if provider_class is NvidiaNimVisualProvider
                else {}
            )
            return provider_class(
                provider_id=provider_id,
                connection=self.connection,
                base_url=base_url,
                credential_ref=credential_ref,
                deployment_mode=deployment_mode,
                api_family=api_family,
                adapter_profile=adapter_profile,
                terms_mode=str(account.get("termsMode") or "unspecified"),
                pricing_mode=str(account.get("pricingMode") or "unknown"),
                transport=self.transport,
                **visual_options,
            )
        if provider_family in {"ollama", "local_ollama"} or api_format == "ollama":
            endpoint_base_url = (
                str(account.get("baseUrl") or "")
                if api_format == "ollama" and provider_id not in {"ollama", "local_ollama"}
                else base_url
            )
            return OllamaProvider(
                provider_id=provider_id,
                base_url=endpoint_base_url,
                credential_ref=credential_ref,
            )
        if provider_family in {"openai", "openai_api"}:
            return OpenAIAPIProvider(base_url=base_url, credential_ref=credential_ref)
        if provider_family == "anthropic_api":
            return AnthropicAPIProvider(
                provider_id=provider_id,
                base_url=base_url,
                credential_ref=credential_ref,
            )
        if provider_family == "openrouter":
            return OpenRouterProvider(
                provider_id=provider_id,
                base_url=base_url,
                credential_ref=credential_ref,
            )
        if provider_family == "gemini":
            return GeminiProvider(
                provider_id=provider_id,
                base_url=base_url,
                credential_ref=credential_ref,
            )
        if provider_family == "litellm":
            return LiteLLMAdapter(base_url=base_url, credential_ref=credential_ref)
        if provider_family == "azure_openai" or api_format == "azure_openai":
            return AzureOpenAIProvider(base_url=base_url, credential_ref=credential_ref or None)
        return OpenAICompatibleProvider(
            provider_id=provider_id,
            base_url=base_url,
            credential_ref=credential_ref,
            use_legacy_fallbacks=False,
        )

    def resolve_for_execution(self, provider_id: str) -> Any:
        """Resolve only an explicitly enabled account for a productive transport call."""
        account = self.accounts.get_provider_account(provider_id)
        if not account.get("enabled"):
            raise ProviderAccountDisabledError(provider_id)
        return self.resolve(provider_id)

    @staticmethod
    def _runtime_configuration(account: dict[str, Any]):
        return runtime_provider_configuration_for_account(account)

    def _connection_configuration(self, account: dict[str, Any]) -> tuple[str | None, str | None]:
        configuration = self._runtime_configuration(account)
        provider_id = str(account["providerId"])
        if configuration and configuration.resolution_error:
            raise ProviderConfigurationConflictError(provider_id=provider_id)
        provider_family = str(account.get("providerFamily") or "")
        deployment_mode = str(account.get("deploymentMode") or "")
        api_family = str(account.get("apiFamily") or "")
        account_base_url = str(account.get("baseUrl") or "").strip()
        family_default_base_url = (
            known_provider_default_base_url(provider_family)
            if api_family in {"chat_completions", "embeddings"}
            and (provider_id == provider_family or deployment_mode == "hosted_trial")
            else None
        )
        base_url = (
            (configuration.value("baseUrl") if configuration else None)
            or account_base_url
            or family_default_base_url
            or None
        )
        credential_ref = (
            (configuration.configured_env_ref("apiKey") if configuration else None)
            or str(account.get("credentialRef") or "").strip()
            or None
        )
        return base_url, credential_ref
