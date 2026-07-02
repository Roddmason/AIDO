"""Integra la API Messages de Anthropic con su esquema nativo (no estilo OpenAI).

Habla el protocolo propio de Anthropic: auth `x-api-key` + `anthropic-version`, endpoint
`/messages`, mensajes `system` extraidos aparte y `max_tokens` obligatorio. Suma los
tokens de cacheo (creacion + lectura) en `cached_input_tokens` y, como no hay tarifa
publicada aqui, deja el costo como desconocido. La autoridad de ejecución vive en la
política SQLite de runtime; este proveedor valida config/credencial y redacta payloads.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration
from local_control_center.shared.redaction import redact_secrets

from .base import (
    CostEstimate,
    ModelInfo,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderHealth,
    UsageRecord,
)

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class AnthropicAPIProvider(ModelProvider):
    """Proveedor para la API Messages de Anthropic con su esquema y auth propios."""

    provider_id = "anthropic_api"

    def __init__(self, *, base_url: str | None = None, credential_ref: str | None = None):
        runtime_configuration = runtime_provider_configuration(self.provider_id)
        self.base_url = (
            base_url
            or (runtime_configuration.value("baseUrl") if runtime_configuration else None)
            or os.environ.get("AIDO_ANTHROPIC_BASE_URL")
            or DEFAULT_ANTHROPIC_BASE_URL
        ).rstrip("/")
        self.credential_ref = (
            credential_ref
            or (runtime_configuration.configured_env_ref("apiKey") if runtime_configuration else None)
            or "env:AIDO_ANTHROPIC_API_KEY"
        )
        self.credential_resolver = CredentialResolver()

    def _credential(self) -> str:
        return self.credential_resolver.resolve(self.credential_ref).value or ""

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._credential(),
            "anthropic-version": ANTHROPIC_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers=self._headers(),
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def health_check(self) -> ProviderHealth:
        """Valida credencial/URL y prueba `/models`; la política runtime se aplica aguas arriba."""
        credential = self.credential_resolver.resolve(self.credential_ref, fetch=False)
        if credential.status == "invalid":
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message=credential.message,
            )
        if credential.status in {"missing", "unsupported", "unknown"}:
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message=f"Credential ref {self.credential_ref} is {credential.status}",
            )
        if not self.base_url:
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message="Anthropic base URL is not configured.",
            )
        try:
            self._get_json("/models")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return ProviderHealth(
                providerId=self.provider_id,
                status="not_available",
                healthStatus="offline",
                message=f"Anthropic /models health check failed: {error.__class__.__name__}",
            )
        return ProviderHealth(
            providerId=self.provider_id,
            status="available",
            healthStatus="healthy",
            message="Anthropic /models responded",
        )

    def list_models(self) -> list[ModelInfo]:
        """Descubre modelos via `/models`."""
        payload = self._get_json("/models")
        models = payload.get("data", []) if isinstance(payload, dict) else []
        return [
            ModelInfo(
                providerId=self.provider_id,
                model=str(item.get("id")),
                displayName=str(item.get("display_name") or item.get("id")),
                source="provider",
            )
            for item in models
            if isinstance(item, dict) and item.get("id")
        ]

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Postea a `/messages` con el payload nativo de Anthropic y normaliza la respuesta."""
        if not self.base_url or not self._credential():
            raise RuntimeError("Anthropic provider is missing base_url or credential_ref")
        payload = json.dumps(self._messages_payload(request)).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/messages",
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return ModelResponse(
            providerId=self.provider_id,
            model=request.model,
            content=self._content_text(raw),
            usage=self.parse_usage(raw),
            rawResponse=redact_secrets(raw),
        )

    def _messages_payload(self, request: ModelRequest) -> dict[str, Any]:
        system_messages: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            role = str(message.get("role") or "user")
            content = message.get("content", "")
            if role == "system":
                system_messages.append(self._text_content(content))
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            messages.append({"role": role, "content": content})
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": messages,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if system_messages:
            payload["system"] = "\n\n".join(item for item in system_messages if item)
        return payload

    @staticmethod
    def _text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") in {None, "text"}
            ]
            return "\n".join(part for part in parts if part)
        return str(content or "")

    @classmethod
    def _content_text(cls, raw_response: Any) -> str:
        content = raw_response.get("content") if isinstance(raw_response, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
        return ""

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        """Sin tabla de precios local: devuelve costo desconocido marcando proveedor y modelo."""
        return CostEstimate(estimatedCostUsd=None, source=f"unknown:{self.provider_id}:{model}")

    def parse_usage(self, raw_response: Any) -> UsageRecord:
        """Lee el `usage` de Anthropic; agrega los tokens de cacheo (creacion + lectura) en cached_input_tokens."""
        usage = raw_response.get("usage", {}) if isinstance(raw_response, dict) else {}
        token_fields = {
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        }
        if not isinstance(usage, dict) or not any(field in usage for field in token_fields):
            return UsageRecord(
                rawUsage={
                    "usage_source": "unknown",
                    "reason": "provider_response_missing_usage",
                }
            )
        input_tokens = _int_value(usage.get("input_tokens"))
        cached_input_tokens = _int_value(usage.get("cache_creation_input_tokens")) + _int_value(
            usage.get("cache_read_input_tokens")
        )
        output_tokens = _int_value(usage.get("output_tokens"))
        total_tokens = input_tokens + cached_input_tokens + output_tokens
        return UsageRecord(
            inputTokens=input_tokens,
            cachedInputTokens=cached_input_tokens,
            outputTokens=output_tokens,
            totalTokens=total_tokens,
            rawUsage=redact_secrets({**usage, "usage_source": "provider"}),
        )
