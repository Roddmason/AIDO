from __future__ import annotations

from .base import CostEstimate, ModelInfo, ModelProvider, ModelRequest, ModelResponse, ProviderHealth, UsageRecord


class AnthropicAPIProvider(ModelProvider):
    provider_id = "anthropic_api"

    def __init__(self, *, mock: bool = False):
        self.mock = mock

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(providerId=self.provider_id, status="optional", healthStatus="unknown", message="Anthropic adapter stub; real calls are not enabled by default")

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                providerId=self.provider_id,
                model="configured_model",
                displayName="Configured Anthropic model",
                contextWindow=200000,
                maxOutputTokens=4096,
                supportsJson=True,
                supportsStreaming=True,
                supportsReasoning=True,
                source="manual_seed",
            )
        ]

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        usage = UsageRecord(inputTokens=1, outputTokens=1, totalTokens=2, rawUsage={"usage_source": "mock"})
        return ModelResponse(providerId=self.provider_id, model=request.model, content="mock anthropic response", usage=usage, rawResponse={"mock": True})

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        return CostEstimate(estimatedCostUsd=None, source="unknown")

    def parse_usage(self, raw_response):
        return UsageRecord(rawUsage={"usage_source": "not_available"})
