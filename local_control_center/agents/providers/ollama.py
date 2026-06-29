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
from urllib.error import URLError
from urllib.request import Request, urlopen

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


def _public_error(error: BaseException) -> str:
    return str(redact_secrets(f"{error.__class__.__name__}: {error}"))


class OllamaProvider(ModelProvider):
    """Proveedor para un Ollama local o remoto; habla su API nativa, sin costo, con auth opcional."""

    provider_id = "ollama"

    def __init__(self, *, base_url: str | None = None, credential_ref: str | None = None):
        runtime_configuration = runtime_provider_configuration("ollama")
        self.base_url = (
            base_url
            or (runtime_configuration.value("baseUrl") if runtime_configuration else None)
            or os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        ).rstrip("/")
        self.credential_ref = credential_ref or ""
        self.credential_resolver = CredentialResolver()

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
            with urlopen(request, timeout=2):
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
        headers, blocking_reason = self._auth_headers_or_block()
        if blocking_reason:
            return []
        try:
            request = Request(f"{self.base_url}/api/tags", headers=headers, method="GET")
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
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

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Postea a `/api/chat` sin streaming y deriva el uso de los contadores `*_eval_count`.

        Raises:
            RuntimeError: si hay un credentialRef configurado que no resuelve (falla cerrado).
        """
        headers, blocking_reason = self._auth_headers_or_block()
        if blocking_reason:
            raise RuntimeError(blocking_reason)
        payload = json.dumps(
            {
                "model": request.model,
                "messages": request.messages,
                "stream": False,
                "options": {"temperature": request.temperature} if request.temperature is not None else {},
            }
        ).encode("utf-8")
        http_request = Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **headers,
            },
            method="POST",
        )
        with urlopen(http_request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
        message = raw.get("message") if isinstance(raw, dict) else {}
        content = str((message or {}).get("content") or "")
        usage = UsageRecord(
            inputTokens=int(raw.get("prompt_eval_count") or 0) if isinstance(raw, dict) else 0,
            outputTokens=int(raw.get("eval_count") or 0) if isinstance(raw, dict) else 0,
            totalTokens=(
                int(raw.get("prompt_eval_count") or 0) + int(raw.get("eval_count") or 0)
                if isinstance(raw, dict)
                else 0
            ),
            rawUsage={
                "usage_source": "provider",
                "prompt_eval_count": raw.get("prompt_eval_count"),
                "eval_count": raw.get("eval_count"),
            }
            if isinstance(raw, dict)
            else {"usage_source": "provider"},
        )
        return ModelResponse(
            providerId=self.provider_id, model=request.model, content=content, usage=usage, rawResponse=raw
        )

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        """Ejecucion local: costo cero, etiquetado como `local`."""
        return CostEstimate(estimatedCostUsd=0.0, source="local")

    def parse_usage(self, raw_response):
        """Ollama no entrega `usage` estandar: marca el origen como estimado y delega el conteo al chat."""
        return UsageRecord(rawUsage={"usage_source": "estimated"})
