"""Conecta con un servidor Ollama local o remoto hablando su API nativa (no estilo OpenAI).

Implementa el contrato directamente contra los endpoints `/api/tags` y `/api/chat` de
Ollama. Por defecto (local) no usa credencial; cuando se configura un ``credentialRef``
(despliegue remoto tras auth) adjunta una cabecera ``Authorization: Bearer`` resuelta
sin exponer el secreto. Trata el costo como gratis y deriva el uso de los contadores
`*_eval_count`.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any
from urllib.error import URLError
from urllib.request import Request

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.model_output_text import strip_reasoning_blocks
from local_control_center.agents.runtime_provider_config import (
    DEFAULT_OLLAMA_BASE_URL,
    runtime_provider_configuration,
)
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


def _public_error(error: BaseException) -> str:
    return str(redact_secrets(f"{error.__class__.__name__}: provider request failed"))


class OllamaProvider(ModelProvider):
    """Proveedor para un Ollama local o remoto; habla su API nativa, sin costo, con auth opcional."""

    provider_id = "ollama"
    max_response_bytes = MAX_PROVIDER_RESPONSE_BYTES

    def __init__(
        self,
        *,
        provider_id: str = "ollama",
        base_url: str | None = None,
        credential_ref: str | None = None,
    ):
        is_legacy_provider = provider_id in {"ollama", "local_ollama"}
        runtime_configuration = runtime_provider_configuration("ollama") if is_legacy_provider else None
        resolved_base_url = (
            base_url
            if base_url is not None
            else (
                (runtime_configuration.value("baseUrl") if runtime_configuration else None)
                or os.environ.get("OLLAMA_BASE_URL")
                or os.environ.get("OLLAMA_HOST")
                or DEFAULT_OLLAMA_BASE_URL
                if is_legacy_provider
                else ""
            )
        )
        self.provider_id = provider_id
        self.base_url = resolved_base_url.rstrip("/") if resolved_base_url else ""
        self.credential_ref = credential_ref or ""
        self.credential_resolver = CredentialResolver()
        self.invocation_slot: Callable[[], AbstractContextManager[object]] = nullcontext

    def _base_url_blocking_reason(self) -> str | None:
        if self.base_url:
            return None
        return "Ollama base URL is not configured."

    def _auth_headers_or_block(self) -> tuple[dict[str, str], str | None]:
        """Devuelve (cabecera Bearer, motivo de bloqueo) resolviendo el ``credentialRef``.

        Sin credencial -> sin header (Ollama local). Con credencial configurada pero irresoluble
        falla cerrado (motivo, sin header): no degrada a una sonda anonima contra un remoto que
        exige auth (evita reportar disponible sin credencial valida).
        """
        if not self.credential_ref:
            return {}, None
        resolution = self.credential_resolver.resolve(self.credential_ref)
        if resolution.configured:
            return {"Authorization": f"Bearer {resolution.value}"}, None
        return {}, f"Ollama credentialRef is {resolution.status}; configure the remote access token."

    def health_check(self) -> ProviderHealth:
        """Sondea `/api/tags` con timeout corto; offline si el servidor no responde.

        Falla cerrado como ``misconfigured`` (sin sondear) si hay credentialRef irresoluble.
        """
        if blocking_reason := self._base_url_blocking_reason():
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message=blocking_reason,
            )
        headers, blocking_reason = self._auth_headers_or_block()
        if blocking_reason:
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message=blocking_reason,
            )
        try:
            request = Request(f"{self.base_url}/api/tags", headers=headers, method="GET")
            with urlopen_fail_closed(request, timeout=2):
                pass
        except (OSError, TimeoutError, URLError) as error:
            return ProviderHealth(
                providerId=self.provider_id,
                status="not_available",
                healthStatus="offline",
                message=_public_error(error),
            )
        return ProviderHealth(
            providerId=self.provider_id,
            status="available",
            healthStatus="healthy",
            message="Ollama responded",
        )

    def list_models(self) -> list[ModelInfo]:
        """Lista los modelos descargados desde `/api/tags`; [] si falla la consulta o falta credencial."""
        if self._base_url_blocking_reason():
            return []
        headers, blocking_reason = self._auth_headers_or_block()
        if blocking_reason:
            return []
        try:
            request = Request(f"{self.base_url}/api/tags", headers=headers, method="GET")
            with urlopen_fail_closed(request, timeout=2) as response:
                payload = json.loads(read_bounded(response, limit=self.max_response_bytes).decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError):
            return []
        return [
            ModelInfo(
                providerId=self.provider_id,
                model=str(item.get("name")),
                displayName=str(item.get("name")),
                source="provider",
            )
            for item in payload.get("models", [])
            if item.get("name")
        ]

    @staticmethod
    def _chat_body(request: ModelRequest) -> dict[str, Any]:
        """Arma el body nativo de ``/api/chat``: ``num_predict`` para el tope y ``format`` para JSON."""
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        body: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
            "options": options,
        }
        response_format = request.response_format or {}
        if response_format.get("type") == "json_schema":
            schema = (response_format.get("json_schema") or {}).get("schema")
            if isinstance(schema, dict):
                body["format"] = schema
        elif response_format.get("type") == "json_object":
            body["format"] = "json"
        return body

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Postea a `/api/chat` sin streaming con timeout y lectura acotados.

        Descarta ``thinking`` y bloques ``<think>`` del contenido y marca el uso como ``unknown``
        cuando Ollama no reporta contadores.

        Raises:
            RuntimeError: si hay un credentialRef configurado que no resuelve (falla cerrado).
            LocalRuntimeError: ``local_endpoint_busy`` si la lease del endpoint local no se libera o su
                espera agotó el deadline (``insufficient_time_for_model_load`` con arranque en frío).
            ResponseTooLargeError: si el cuerpo supera ``max_response_bytes``.
        """
        if blocking_reason := self._base_url_blocking_reason():
            raise RuntimeError(blocking_reason)
        headers, blocking_reason = self._auth_headers_or_block()
        if blocking_reason:
            raise RuntimeError(blocking_reason)
        http_request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(self._chat_body(request)).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
            method="POST",
        )
        with self.invocation_slot():
            timeout = request.http_timeout(DEFAULT_CHAT_TIMEOUT_SECONDS)
            with urlopen_fail_closed(http_request, timeout=timeout) as response:
                decoded = json.loads(read_bounded(response, limit=self.max_response_bytes).decode("utf-8"))
        raw = decoded if isinstance(decoded, dict) else {}
        message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
        content, reasoning_chars = strip_reasoning_blocks(str(message.get("content") or ""))
        thinking = str(message.get("thinking") or "").strip()
        prompt_count = raw.get("prompt_eval_count")
        eval_count = raw.get("eval_count")
        reported = prompt_count is not None or eval_count is not None
        usage = UsageRecord(
            inputTokens=int(prompt_count or 0),
            outputTokens=int(eval_count or 0),
            totalTokens=int(prompt_count or 0) + int(eval_count or 0),
            rawUsage={
                "usage_source": "provider" if reported else "unknown",
                "prompt_eval_count": prompt_count,
                "eval_count": eval_count,
            },
        )
        public_message = {key: value for key, value in message.items() if key != "thinking"}
        done_reason = raw.get("done_reason")
        return ModelResponse(
            providerId=self.provider_id,
            model=request.model,
            content=content,
            usage=usage,
            rawResponse=redact_secrets({**raw, "message": {**public_message, "content": content}}),
            finishReason=str(done_reason) if done_reason is not None else None,
            reasoningPresent=bool(thinking) or reasoning_chars > 0,
        )

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        """Ejecucion local: costo cero, etiquetado como `local`."""
        return CostEstimate(estimatedCostUsd=0.0, source="local")

    def parse_usage(self, raw_response):
        """Ollama no entrega `usage` estandar: marca el origen como estimado y delega el conteo al chat."""
        return UsageRecord(rawUsage={"usage_source": "estimated"})
