"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen

from local_control_center.shared.redaction import redact_secrets
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration

from .base import CostEstimate, ModelInfo, ModelProvider, ModelRequest, ModelResponse, ProviderHealth, UsageRecord


def _public_error(error: BaseException) -> str:
    return str(redact_secrets(f"{error.__class__.__name__}: {error}"))


class OllamaProvider(ModelProvider):
    provider_id = "ollama"

    def __init__(self, *, base_url: str | None = None):
        runtime_configuration = runtime_provider_configuration("ollama")
        self.base_url = (
            base_url
            or (runtime_configuration.value("baseUrl") if runtime_configuration else None)
            or os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://localhost:11434"
        ).rstrip("/")

    def health_check(self) -> ProviderHealth:
        try:
            request = Request(f"{self.base_url}/api/tags", method="GET")
            with urlopen(request, timeout=2):
                pass
        except (OSError, TimeoutError, URLError) as error:
            return ProviderHealth(providerId=self.provider_id, status="not_available", healthStatus="offline", message=_public_error(error))
        return ProviderHealth(providerId=self.provider_id, status="available", healthStatus="healthy", message="Ollama responded")

    def list_models(self) -> list[ModelInfo]:
        try:
            request = Request(f"{self.base_url}/api/tags", method="GET")
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError):
            return []
        return [
            ModelInfo(providerId=self.provider_id, model=str(item.get("name")), displayName=str(item.get("name")), source="provider")
            for item in payload.get("models", [])
            if item.get("name")
        ]

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
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
            headers={"Content-Type": "application/json", "Accept": "application/json"},
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
        return ModelResponse(providerId=self.provider_id, model=request.model, content=content, usage=usage, rawResponse=raw)

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        return CostEstimate(estimatedCostUsd=0.0, source="local")

    def parse_usage(self, raw_response):
        return UsageRecord(rawUsage={"usage_source": "estimated"})
