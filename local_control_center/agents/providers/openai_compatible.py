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
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.model_output_text import strip_reasoning_blocks
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
from .http_transport import (
    DEFAULT_CHAT_TIMEOUT_SECONDS,
    MAX_PROVIDER_RESPONSE_BYTES,
    read_bounded,
    urlopen_fail_closed,
)

PROVIDER_USER_AGENT = "AIDO-ModelGateway/1.0"
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


def _chat_choice(raw: Any) -> tuple[str, str | None, bool]:
    """Extrae (contenido sin razonamiento, finish_reason, hubo razonamiento no vacío) del primer choice."""
    choices = raw.get("choices") if isinstance(raw, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", None, False
    choice = choices[0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content, reasoning_chars = strip_reasoning_blocks(str(message.get("content") or ""))
    reasoning_field = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
    finish_reason = choice.get("finish_reason")
    return (
        content,
        str(finish_reason) if finish_reason is not None else None,
        bool(reasoning_field) or reasoning_chars > 0,
    )


def _without_reasoning(raw: Any, content: str) -> Any:
    """Copia de la respuesta cuyo primer choice no conserva razonamiento ni bloques ``<think>``."""
    choices = raw.get("choices") if isinstance(raw, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return raw
    first = choices[0]
    if not isinstance(first.get("message"), dict):
        return raw
    message = {
        key: value for key, value in first["message"].items() if key not in {"reasoning_content", "reasoning"}
    }
    message["content"] = content
    return {**raw, "choices": [{**first, "message": message}, *choices[1:]]}


class OpenAICompatibleProvider(ModelProvider):
    """Proveedor base que habla la API estilo OpenAI; las variantes solo ajustan url/credencial."""

    max_response_bytes = MAX_PROVIDER_RESPONSE_BYTES
    send_output_limit = False

    def __init__(
        self,
        *,
        provider_id: str = "openai_compatible",
        base_url: str | None = None,
        credential_ref: str | None = None,
        credential_required: bool = True,
        use_legacy_fallbacks: bool = True,
    ):
        runtime_configuration = runtime_provider_configuration(provider_id) if use_legacy_fallbacks else None
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
            else (runtime_configuration.configured_env_ref("apiKey") if runtime_configuration else "")
        )
        self.provider_id = provider_id
        self.base_url = resolved_base_url.rstrip("/")
        self.credential_ref = resolved_credential_ref
        self.credential_required = credential_required
        self.credential_resolver = CredentialResolver()
        # Recibe el deadline monótono de la llamada; `nullcontext` lo acepta como `enter_result` y no espera nada.
        self.invocation_slot: Callable[[float | None], AbstractContextManager[object]] = nullcontext

    def _credential(self) -> str:
        return self.credential_resolver.resolve(self.credential_ref).value or ""

    def _auth_headers(self) -> dict[str, str]:
        """Cabeceras de autenticación del dialecto OpenAI (`Authorization: Bearer`).

        Punto de extensión: las variantes con otro esquema (p. ej. Azure OpenAI con `api-key`)
        sobreescriben este método sin duplicar el resto del transporte.
        """
        credential = self._credential()
        return {"Authorization": f"Bearer {credential}"} if credential else {}

    def _request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Cabeceras comunes de toda petición: auth, JSON y la identidad del cliente.

        Sin ``User-Agent`` propio urllib se anuncia como ``Python-urllib/3.x``, y hay gateways que
        responden 403 a ese agente genérico; identificar al cliente es además lo correcto para que
        el operador reconozca el tráfico de AIDO en los logs del proveedor.
        """
        return {
            **self._auth_headers(),
            "Accept": "application/json",
            "User-Agent": PROVIDER_USER_AGENT,
            **(extra or {}),
        }

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
            headers=self._request_headers(),
            method="GET",
        )
        try:
            with urlopen_fail_closed(request, timeout=10) as response:
                json.loads(read_bounded(response, limit=self.max_response_bytes).decode("utf-8"))
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
            headers=self._request_headers(),
            method="GET",
        )
        try:
            with urlopen_fail_closed(request, timeout=10) as response:
                payload = json.loads(read_bounded(response, limit=self.max_response_bytes).decode("utf-8"))
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

    def _chat_body(self, request: ModelRequest) -> dict[str, Any]:
        """Arma el body OpenAI explícito: solo campos del protocolo, sin ``metadata`` ni alias camelCase.

        ``max_tokens`` solo viaja con ``send_output_limit`` (cuentas de runtime local, fijado por la
        factory): las APIs remotas exigen otro campo para modelos de razonamiento y un tope pequeño
        les vaciaría la respuesta. ``extra_body`` agrega campos propios del servidor (p. ej.
        ``chat_template_kwargs`` de llama.cpp) sin poder pisar los del protocolo.
        """
        body: dict[str, Any] = {"model": request.model, "messages": request.messages, "stream": False}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if self.send_output_limit and request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            body["response_format"] = request.response_format
        for key, value in request.extra_body.items():
            body.setdefault(key, value)
        return body

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Postea a `/chat/completions` con timeout y lectura acotados y descarta el razonamiento.

        La llamada HTTP corre dentro de ``invocation_slot`` (lease de concurrencia de cuentas locales) y su
        timeout se calcula ya dentro del slot, con lo que queda del deadline de la llamada.

        Raises:
            RuntimeError: si falta la URL o una credencial declarada no resuelve.
            LocalRuntimeError: ``local_endpoint_busy`` si la lease del endpoint local no se libera o su
                espera agotó el deadline (``insufficient_time_for_model_load`` con arranque en frío).
            ResponseTooLargeError: si el cuerpo supera ``max_response_bytes``.
        """
        credential = self._credential()
        if (
            not self.base_url
            or (self.credential_required and not credential)
            or (self.credential_ref and not credential)
        ):
            raise RuntimeError("Provider is missing base_url or credential_ref")
        payload = json.dumps(self._chat_body(request)).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers=self._request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with self.invocation_slot(request.deadline_monotonic):
            timeout = request.http_timeout(DEFAULT_CHAT_TIMEOUT_SECONDS)
            with urlopen_fail_closed(http_request, timeout=timeout) as response:
                raw = json.loads(read_bounded(response, limit=self.max_response_bytes).decode("utf-8"))
        content, finish_reason, reasoning_present = _chat_choice(raw)
        return ModelResponse(
            providerId=self.provider_id,
            model=request.model,
            content=content,
            usage=self.parse_usage(raw),
            rawResponse=redact_secrets(_without_reasoning(raw, content)),
            finishReason=finish_reason,
            reasoningPresent=reasoning_present,
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
