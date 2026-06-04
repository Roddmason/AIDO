from __future__ import annotations

from .base import CostEstimate, ModelInfo, ModelProvider, ModelRequest, ModelResponse, ProviderHealth, UsageRecord


class AnthropicAPIProvider(ModelProvider):
    provider_id = "anthropic_api"

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            providerId=self.provider_id,
            status="not_implemented",
            healthStatus="unavailable",
            message="Anthropic provider is declared but no real adapter is implemented.",
        )

    def list_models(self) -> list[ModelInfo]:
        return []

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("Anthropic provider is declared but no real adapter is implemented.")

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        return CostEstimate(estimatedCostUsd=None, source="unknown")

    def parse_usage(self, raw_response):
        return UsageRecord(rawUsage={"usage_source": "not_available"})
