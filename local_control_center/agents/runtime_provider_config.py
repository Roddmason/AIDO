"""Resolves runtime provider configuration from environment variables, secrets-safe.

Declares the env-var contract for each runtime provider (CLI commands, API keys, base
URLs, models) and reads the current process environment into typed configuration
objects. Secret values are never returned to clients: presence is reported as a
truncated SHA-256 fingerprint instead of the raw value.
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

    @property
    def configured(self) -> bool:
        """True when every required variable has a value."""
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
        return "configured" if self.configured else "configuration_required"

    @property
    def reason(self) -> str:
        """Human-readable explanation naming the missing variables when not configured."""
        if self.configured:
            return "Required runtime provider configuration is present."
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
                return f"env:{variable.spec.name}"
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
        provider_id="nvidia_nim",
        display_name="NVIDIA NIM / Build",
        kind="api",
        variables=(
            RuntimeConfigVariableSpec("apiKey", "AIDO_NVIDIA_API_KEY", secret=True),
            RuntimeConfigVariableSpec("baseUrl", "AIDO_NVIDIA_BASE_URL", secret=False),
            RuntimeConfigVariableSpec("model", "AIDO_NVIDIA_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="anthropic_api",
        display_name="Anthropic API",
        kind="api",
        variables=(
            RuntimeConfigVariableSpec("apiKey", "AIDO_ANTHROPIC_API_KEY", secret=True),
            RuntimeConfigVariableSpec("model", "AIDO_ANTHROPIC_MODEL", secret=False),
        ),
    ),
    RuntimeProviderConfigSpec(
        provider_id="ollama",
        display_name="Ollama Local",
        kind="local",
        variables=(RuntimeConfigVariableSpec("baseUrl", "AIDO_OLLAMA_BASE_URL", secret=False),),
    ),
    RuntimeProviderConfigSpec(
        provider_id="codex_cli",
        display_name="Codex CLI",
        kind="cli",
        variables=(RuntimeConfigVariableSpec("command", "AIDO_CODEX_COMMAND", secret=False),),
    ),
    RuntimeProviderConfigSpec(
        provider_id="claude_code_cli",
        display_name="Claude Code CLI",
        kind="cli",
        variables=(RuntimeConfigVariableSpec("command", "AIDO_CLAUDE_COMMAND", secret=False),),
    ),
    RuntimeProviderConfigSpec(
        provider_id="openhands",
        display_name="OpenHands",
        kind="cli",
        variables=(
            RuntimeConfigVariableSpec("command", "AIDO_OPENHANDS_COMMAND", secret=False),
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
            RuntimeConfigVariableSpec("command", "AIDO_SWE_AGENT_COMMAND", secret=False),
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


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def runtime_provider_configuration(
    provider_id: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeProviderConfiguration | None:
    """Resolve one provider's configuration from `environ` (default: `os.environ`).

    Returns None when the provider id is not in the spec catalog.
    """
    spec = _CONFIG_SPECS_BY_PROVIDER.get(provider_id)
    if spec is None:
        return None
    source = environ or os.environ
    variables = tuple(
        RuntimeConfigVariable(spec=variable_spec, value=_env_value(source, variable_spec.name))
        for variable_spec in spec.variables
    )
    return RuntimeProviderConfiguration(spec=spec, variables=variables)


def list_runtime_provider_configurations(
    *,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve and serialize every known provider's configuration to client-safe dicts."""
    source = environ or os.environ
    return [
        RuntimeProviderConfiguration(
            spec=spec,
            variables=tuple(
                RuntimeConfigVariable(spec=variable_spec, value=_env_value(source, variable_spec.name))
                for variable_spec in spec.variables
            ),
        ).public_dict()
        for spec in RUNTIME_PROVIDER_CONFIG_SPECS
    ]
