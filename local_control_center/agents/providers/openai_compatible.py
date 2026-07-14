"""Implementa el contrato de proveedor sobre la API de chat estilo OpenAI (`/v1`).

Habla el dialecto OpenAI (endpoints `/models` y `/chat/completions`, auth `Bearer`)
con `urllib`, y sirve de base reutilizable para todos los proveedores compatibles
(OpenAI, OpenRouter, NVIDIA NIM, LiteLLM). La autoridad de ejecución vive en la
política SQLite de runtime; este proveedor valida configuración/credenciales y redacta
secretos antes de exponer payloads.

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
from .http_transport import urlopen_fail_closed

USAGE_TOKEN_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
    "tool_tokens",
)


def _provider_reported_usage(usage: Any) -> bool:
    """Indica si el bloque `usage` trae al menos un contador de tokens reportado por el proveedor."""
    return isinstance(usage, dict) and any(usage.get(key) is not None for key in USAGE_TOKEN_KEYS)


class OpenAICompatibleProvider(ModelProvider):
    """Proveedor base que habla la API estilo OpenAI; las variantes solo ajustan url/credencial."""

    def __init__(
        self,
        *,
        provider_id: str = "openai_compatible",
        base_url: str | None = None,
        credential_ref: str | None = None,
        credential_required: bool = True,
        use_legacy_fallbacks: bool = True,
    ):
        runtime_configuration = (
            runtime_provider_configuration(provider_id) if use_legacy_fallbacks else None
        )
        resolved_base_url = (
            base_url
            if base_url is not None
            else (
                (runtime_configuration.value("baseUrl") if runtime_configuration else None)
                or os.environ.get("AIDO_OPENAI_COMPATIBLE_BASE_URL")
                or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
                or ""
                if use_legacy_fallbacks
                else ""
            )
        )
        resolved_credential_ref = (
            credential_ref
            if credential_ref is not None
            else (
                runtime_configuration.configured_env_ref("apiKey")
                if runtime_configuration
                else ""
            )
        )
        self.provider_id = provider_id
        self.base_url = resolved_base_url.rstrip("/")
        self.credential_ref = resolved_credential_ref
        self.credential_required = credential_required
        self.credential_resolver = CredentialResolver()

    def _credential(self) -> str:
        return self.credential_resolver.resolve(self.credential_ref).value or ""

    def _auth_headers(self) -> dict[str, str]:
        """Cabeceras de autenticación del dialecto OpenAI (`Authorization: Bearer`).

        Punto de extensión: las variantes con otro esquema (p. ej. Azure OpenAI con `api-key`)
        sobreescriben este método sin duplicar el resto del transporte.
        """
        credential = self._credential()
        return {"Authorization": f"Bearer {credential}"} if credential else {}

    def health_check(self) -> ProviderHealth:
        """Valida config/credencial y prueba `/models`; la política runtime se aplica aguas arriba."""
        if not self.base_url:
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message="Base URL is not configured",
            )
        if self.credential_ref:
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
        elif self.credential_required:
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message="Credential ref is not configured",
            )
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={**self._auth_headers(), "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen_fail_closed(request, timeout=10) as response:
                json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return ProviderHealth(
                providerId=self.provider_id,
                status="not_available",
                healthStatus="offline",
                message=f"Provider /models health check failed: {error.__class__.__name__}",
            )
        return ProviderHealth(
            providerId=self.provider_id,
            status="available",
            healthStatus="healthy",
            message="Provider /models responded",
        )

    def list_models(self) -> list[ModelInfo]:
        """Lee `/models`; devuelve [] si no hay URL o la peticion falla."""
        if not self.base_url:
            return []
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={**self._auth_headers(), "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen_fail_closed(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return []
        models = payload.get("data", []) if isinstance(payload, dict) else []
        return [
            ModelInfo(
                providerId=self.provider_id,
                model=str(item.get("id") or item.get("model")),
                displayName=str(item.get("id") or item.get("model")),
                source="provider",
            )
            for item in models
            if isinstance(item, dict) and (item.get("id") or item.get("model"))
        ]

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Postea a `/chat/completions` y normaliza la respuesta. Raises si falta credencial/URL."""
        credential = self._credential()
        if not self.base_url or (self.credential_required and not credential) or (
            self.credential_ref and not credential
        ):
            raise RuntimeError("Provider is missing base_url or credential_ref")
        payload = json.dumps(request.model_dump(by_alias=True, exclude_none=True)).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                **self._auth_headers(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urlopen_fail_closed(http_request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = ""
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = str((message or {}).get("content") or "")
        return ModelResponse(
            providerId=self.provider_id,
            model=request.model,
            content=content,
            usage=self.parse_usage(raw),
            rawResponse=redact_secrets(raw),
        )

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        """Sin tabla de precios: deja el costo en None y registra el conteo aproximado de tokens en `source`."""
        token_estimate = sum(len(str(message.get("content", "")).split()) for message in request.messages) * 2
        return CostEstimate(
            estimatedCostUsd=None, source=f"unknown:{self.provider_id}:{model}:{token_estimate}"
        )

    def parse_usage(self, raw_response: Any) -> UsageRecord:
        """Mapea el bloque `usage` de OpenAI (prompt/completion + detalles cacheo/reasoning) a UsageRecord.

        Si el proveedor no reporta uso, marca el origen como ``unknown`` en vez de inventar ceros,
        para no fabricar tokens ni costo aguas abajo.
        """
        usage = raw_response.get("usage", {}) if isinstance(raw_response, dict) else {}
        if not _provider_reported_usage(usage):
            return UsageRecord(
                rawUsage={"usage_source": "unknown", "reason": "provider_response_missing_usage"}
            )
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        reasoning_tokens = int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            or usage.get("reasoning_tokens")
            or 0
        )
        cached_input_tokens = int(
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            or usage.get("cached_input_tokens")
            or 0
        )
        tool_tokens = int(usage.get("tool_tokens") or 0)
        total = int(
            usage.get("total_tokens") or input_tokens + output_tokens + reasoning_tokens + tool_tokens
        )
        return UsageRecord(
            inputTokens=input_tokens,
            cachedInputTokens=cached_input_tokens,
            outputTokens=output_tokens,
            reasoningTokens=reasoning_tokens,
            toolTokens=tool_tokens,
            totalTokens=total,
            rawUsage=redact_secrets({**usage, "usage_source": "provider"}),
        )
