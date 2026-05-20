from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import CostEstimate, ModelInfo, ModelProvider, ModelRequest, ModelResponse, ProviderHealth, UsageRecord


class OllamaProvider(ModelProvider):
    provider_id = "ollama"

    def __init__(self, *, base_url: str | None = None, mock: bool = False):
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.mock = mock

    def health_check(self) -> ProviderHealth:
        if self.mock:
            return ProviderHealth(providerId=self.provider_id, status="available", healthStatus="healthy", message="mock Ollama healthy")
        try:
            request = Request(f"{self.base_url}/api/tags", method="GET")
            with urlopen(request, timeout=2):
                pass
        except (OSError, TimeoutError, URLError) as error:
            return ProviderHealth(providerId=self.provider_id, status="not_available", healthStatus="offline", message=str(error))
        return ProviderHealth(providerId=self.provider_id, status="available", healthStatus="healthy", message="Ollama responded")

    def list_models(self) -> list[ModelInfo]:
        if self.mock:
            return [ModelInfo(providerId=self.provider_id, model="local_default", displayName="Local default", contextWindow=32000, maxOutputTokens=4096, source="mock")]
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
        usage = UsageRecord(inputTokens=1, outputTokens=1, totalTokens=2, rawUsage={"usage_source": "mock"})
        return ModelResponse(providerId=self.provider_id, model=request.model, content="mock local response", usage=usage, rawResponse={"mock": True})

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        return CostEstimate(estimatedCostUsd=0.0, source="local")

    def parse_usage(self, raw_response):
        return UsageRecord(rawUsage={"usage_source": "estimated"})
