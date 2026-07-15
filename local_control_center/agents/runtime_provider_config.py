"""Resolves runtime provider configuration from environment variables, secrets-safe.

Declares the env-var contract for each runtime provider (CLI commands, API keys, base
URLs, models) and reads the current process environment into typed configuration
objects. Secret values are never returned to clients: presence is reported as a
truncated SHA-256 fingerprint instead of the raw value.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeConfigVariableSpec:
    """Declares one configuration variable: its key, env-var name, secrecy, and requiredness."""

    key: str
    name: str
    secret: bool
    required: bool = True
    aliases: tuple[str, ...] = ()


class AmbiguousRuntimeProviderCredentialError(ValueError):
    """Raised when multiple credential variables contain different secret values."""

    code = "ambiguous_runtime_provider_credential"

    def __init__(self, *, provider_id: str, names: tuple[str, ...]):
        self.provider_id = provider_id
        self.names = names
        super().__init__(f"{self.code}:{provider_id}:{','.join(names)}")


@dataclass(frozen=True)
class RuntimeProviderConfigSpec:
    """Declares a provider's identity and the set of configuration variables it expects."""

    provider_id: str
    display_name: str
    kind: str
    variables: tuple[RuntimeConfigVariableSpec, ...]


@dataclass(frozen=True)
class RuntimeConfigVariable:
    """A configuration variable spec paired with its resolved value from the environment."""

    spec: RuntimeConfigVariableSpec
    value: str | None
    resolved_name: str | None = None

    @property
    def configured(self) -> bool:
        """True when a non-empty value was resolved for this variable."""
        return bool(self.value)

    @property
    def fingerprint(self) -> str | None:
        """A truncated SHA-256 fingerprint of the value, or None; never exposes the secret."""
        if not self.value:
            return None
        digest = hashlib.sha256(self.value.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:16]}"

    def public_dict(self) -> dict[str, Any]:
        """Serialize to a client-safe dict (fingerprint instead of the raw value)."""
        return {
            "key": self.spec.key,
            "name": self.spec.name,
            "required": self.spec.required,
            "secret": self.spec.secret,
            "configured": self.configured,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimeProviderConfiguration:
    """A provider spec with all of its variables resolved against the environment."""

    spec: RuntimeProviderConfigSpec
    variables: tuple[RuntimeConfigVariable, ...]
    resolution_error: str | None = None

    @property
    def configured(self) -> bool:
        """True when every required variable has a value.

        CLI command env vars are deprecated overrides, so they are configured only when the override
        itself is present; normal CLI readiness comes from runtime_installations/runtime_accounts.
        """
        if self.resolution_error:
            return False
        if self.spec.kind == "cli":
            return bool(self.value("command"))
        return not self.missing

    @property
    def missing(self) -> list[str]:
        """Env-var names of the required variables that are still unset."""
        return [
            variable.spec.name
            for variable in self.variables
            if variable.spec.required and not variable.configured
        ]

    @property
    def status(self) -> str:
        """Either `configured` or `configuration_required` for status surfaces."""
        if self.spec.kind == "cli" and not self.configured:
            return "override_unset"
        return "configured" if self.configured else "configuration_required"

    @property
    def reason(self) -> str:
        """Human-readable explanation naming the missing variables when not configured."""
        if self.resolution_error:
            return self.resolution_error
        if self.configured:
            if self.spec.kind == "cli":
                return "Deprecated CLI command environment override is present."
            return "Required runtime provider configuration is present."
        if self.spec.kind == "cli":
            return (
                "No deprecated CLI command environment override is set; normal CLI configuration lives "
                "in runtime_installations and runtime_accounts."
            )
        return "Missing required runtime provider configuration or credential: " + ", ".join(self.missing)

    def value(self, key: str) -> str | None:
        """Return the resolved value for a variable key, or None if absent/unset."""
        for variable in self.variables:
            if variable.spec.key == key:
                return variable.value
        return None

    def configured_env_ref(self, key: str) -> str | None:
        """Return an `env:NAME` reference for a configured variable, never the value itself."""
        for variable in self.variables:
            if variable.spec.key == key and variable.configured:
                return f"env:{variable.resolved_name or variable.spec.name}"
        return None

    def required_configuration(self) -> list[str]:
        """Env-var names of all required variables for this provider."""
        return [variable.spec.name for variable in self.variables if variable.spec.required]

    def public_dict(self) -> dict[str, Any]:
        """Serialize the provider and its variables to a client-safe status dict."""
        return {
            "id": self.spec.provider_id,
            "displayName": self.spec.display_name,
            "kind": self.spec.kind,
            "configured": self.configured,
            "status": self.status,
            "reason": self.reason,
            "missing": self.missing,
            "variables": [variable.public_dict() for variable in self.variables],
        }


RUNTIME_PROVIDER_CONFIG_SPECS: tuple[RuntimeProviderConfigSpec, ...] = (
    RuntimeProviderConfigSpec(
        provider_id="openai_compatible",
        display_name="OpenAI-compatible API",
        kind="api",
        variables=(
            RuntimeConfigVariableSpec("baseUrl", "AIDO_OPENAI_COMPATIBLE_BASE_URL", secret=False),
            RuntimeConfigVariableSpec("apiKey", "AIDO_OPENAI_COMPATIBLE_API_KEY", secret=True),
            RuntimeConfigVariableSpec("model", "AIDO_OPENAI_COMPATIBLE_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="openrouter",
        display_name="OpenRouter",
        kind="gateway",
        variables=(
            RuntimeConfigVariableSpec("apiKey", "AIDO_OPENROUTER_API_KEY", secret=True),
            RuntimeConfigVariableSpec("model", "AIDO_OPENROUTER_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="gemini",
        display_name="Google Gemini",
        kind="api",
        variables=(
            RuntimeConfigVariableSpec(
                "apiKey",
                "AIDO_GEMINI_API_KEY",
                secret=True,
                aliases=("GEMINI_API_KEY",),
            ),
            RuntimeConfigVariableSpec("model", "AIDO_GEMINI_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="litellm",
        display_name="LiteLLM Proxy",
        kind="gateway",
        variables=(
            RuntimeConfigVariableSpec(
                "baseUrl",
                "AIDO_LITELLM_BASE_URL",
                secret=False,
                aliases=("LITELLM_BASE_URL",),
            ),
            RuntimeConfigVariableSpec(
                "apiKey",
                "AIDO_LITELLM_API_KEY",
                secret=True,
                required=False,
                aliases=("LITELLM_API_KEY",),
            ),
            RuntimeConfigVariableSpec(
                "model",
                "AIDO_LITELLM_MODEL",
                secret=False,
                required=False,
                aliases=("LITELLM_MODEL",),
            ),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="nvidia_nim",
        display_name="NVIDIA NIM / Build",
        kind="api",
        variables=(
            RuntimeConfigVariableSpec("apiKey", "AIDO_NVIDIA_API_KEY", secret=True),
            RuntimeConfigVariableSpec("baseUrl", "AIDO_NVIDIA_BASE_URL", secret=False, required=False),
            RuntimeConfigVariableSpec("model", "AIDO_NVIDIA_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="anthropic_api",
        display_name="Anthropic API",
        kind="api",
        variables=(
            RuntimeConfigVariableSpec("apiKey", "AIDO_ANTHROPIC_API_KEY", secret=True),
            RuntimeConfigVariableSpec(
                "baseUrl", "AIDO_ANTHROPIC_BASE_URL", secret=False, required=False
            ),
            RuntimeConfigVariableSpec("model", "AIDO_ANTHROPIC_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="ollama",
        display_name="Ollama Local/Remote",
        kind="local",
        variables=(RuntimeConfigVariableSpec("baseUrl", "AIDO_OLLAMA_BASE_URL", secret=False),),
    ),
    RuntimeProviderConfigSpec(
        provider_id="codex_cli",
        display_name="Codex CLI",
        kind="cli",
        variables=(RuntimeConfigVariableSpec("command", "AIDO_CODEX_COMMAND", secret=False, required=False),),
    ),
    RuntimeProviderConfigSpec(
        provider_id="claude_code_cli",
        display_name="Claude Code CLI",
        kind="cli",
        variables=(
            RuntimeConfigVariableSpec("command", "AIDO_CLAUDE_COMMAND", secret=False, required=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="openhands",
        display_name="OpenHands",
        kind="cli",
        variables=(
            RuntimeConfigVariableSpec("command", "AIDO_OPENHANDS_COMMAND", secret=False, required=False),
            RuntimeConfigVariableSpec(
                "issueToPatchArgv",
                "AIDO_OPENHANDS_ISSUE_TO_PATCH_ARGV_JSON",
                secret=False,
                required=False,
            ),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="swe_agent",
        display_name="SWE-agent",
        kind="cli",
        variables=(
            RuntimeConfigVariableSpec("command", "AIDO_SWE_AGENT_COMMAND", secret=False, required=False),
            RuntimeConfigVariableSpec(
                "issueToPatchArgv",
                "AIDO_SWE_AGENT_ISSUE_TO_PATCH_ARGV_JSON",
                secret=False,
                required=False,
            ),
        ),
    ),
)


_CONFIG_SPECS_BY_PROVIDER = {spec.provider_id: spec for spec in RUNTIME_PROVIDER_CONFIG_SPECS}

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

KNOWN_PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic_api": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "kimi": "https://api.moonshot.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "nvidia_nim": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
    "openai_api": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def known_provider_default_base_url(provider_id: str) -> str | None:
    """Return the official default endpoint for known API providers, if AIDO owns one."""
    return KNOWN_PROVIDER_DEFAULT_BASE_URLS.get(str(provider_id or "").strip())


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _resolve_runtime_variable(
    *,
    provider_id: str,
    variable_spec: RuntimeConfigVariableSpec,
    environ: Mapping[str, str],
) -> RuntimeConfigVariable:
    """Resolve one variable with deterministic aliases and secret-safe conflict detection.

    Alias order is precedence order. Gemini can prefer ``AIDO_GEMINI_API_KEY`` and then
    ``GEMINI_API_KEY`` without inspecting or inheriting the broader ``GOOGLE_API_KEY`` variable.
    """
    selectable_names = (variable_spec.name, *variable_spec.aliases)
    observed = {
        name: value
        for name in selectable_names
        if (value := _env_value(environ, name)) is not None
    }
    if variable_spec.secret and len(set(observed.values())) > 1:
        raise AmbiguousRuntimeProviderCredentialError(
            provider_id=provider_id,
            names=tuple(observed),
        )
    for name in selectable_names:
        if value := observed.get(name):
            return RuntimeConfigVariable(
                spec=variable_spec,
                value=value,
                resolved_name=name,
            )
    return RuntimeConfigVariable(spec=variable_spec, value=None)


def runtime_provider_configuration(
    provider_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    raise_on_ambiguity: bool = False,
) -> RuntimeProviderConfiguration | None:
    """Resolve one provider's configuration from `environ` (default: `os.environ`).

    Returns None when the provider id is not in the spec catalog.
    """
    spec = _CONFIG_SPECS_BY_PROVIDER.get(provider_id)
    if spec is None:
        return None
    source = os.environ if environ is None else environ
    try:
        variables = tuple(
            _resolve_runtime_variable(
                provider_id=provider_id,
                variable_spec=variable_spec,
                environ=source,
            )
            for variable_spec in spec.variables
        )
    except AmbiguousRuntimeProviderCredentialError as error:
        if raise_on_ambiguity:
            raise
        names = ", ".join(error.names)
        return RuntimeProviderConfiguration(
            spec=spec,
            variables=tuple(
                RuntimeConfigVariable(spec=variable_spec, value=None)
                for variable_spec in spec.variables
            ),
            resolution_error=(
                f"Conflicting credential environment variables are set for {spec.display_name}: "
                f"{names}. Keep one value or make them identical."
            ),
        )
    return RuntimeProviderConfiguration(spec=spec, variables=variables)


def runtime_provider_configuration_for_account(
    account: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeProviderConfiguration | None:
    """Resolve legacy env configuration only for the canonical provider account.

    Endpoint-scoped accounts carry their own persisted base URL and credential reference;
    they must not inherit a provider family's process-global configuration.
    """
    provider_id = str(account.get("providerId") or "").strip()
    provider_family = str(account.get("providerFamily") or "").strip()
    if not provider_id or provider_id != provider_family:
        return None
    return runtime_provider_configuration(provider_id, environ=environ)


def list_runtime_provider_configurations(
    *,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve and serialize every known provider's configuration to client-safe dicts."""
    source = os.environ if environ is None else environ
    configurations: list[dict[str, Any]] = []
    for spec in RUNTIME_PROVIDER_CONFIG_SPECS:
        configuration = runtime_provider_configuration(spec.provider_id, environ=source)
        if configuration is not None:
            configurations.append(configuration.public_dict())
    return configurations
