from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration

from .base import CostEstimate, ModelInfo, ModelRequest, ProviderHealth
from .openai_compatible import OpenAICompatibleProvider


class NvidiaNimProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        base_url: str | None = None,
        credential_ref: str | None = None,
    ):
        runtime_configuration = runtime_provider_configuration("nvidia_nim")
        super().__init__(
            provider_id="nvidia_nim",
            base_url=base_url or (runtime_configuration.value("baseUrl") if runtime_configuration else None) or "https://integrate.api.nvidia.com/v1",
            credential_ref=credential_ref
            or (runtime_configuration.configured_env_ref("apiKey") if runtime_configuration else None)
            or "NVIDIA_NIM_API_KEY",
        )
        self.connection = connection

    def list_models(self) -> list[ModelInfo]:
        return super().list_models()

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        return CostEstimate(estimatedCostUsd=0.0, source="nvidia_nim_free_tier_unknown")

    def handle_error(self, *, status_code: int, message: str, model: str) -> ProviderHealth:
        health = "degraded" if status_code == 429 else "offline"
        if status_code == 429 and self.connection is not None:
            QuotaManager(self.connection).record_rate_limit(provider_id=self.provider_id, model=model, retry_after_seconds=300)
        return ProviderHealth(providerId=self.provider_id, status="rate_limited" if status_code == 429 else "error", healthStatus=health, message=message)

    def parse_usage(self, raw_response: Any):
        usage = super().parse_usage(raw_response)
        if usage.total_tokens == 0 and isinstance(raw_response, dict):
            text = str(raw_response)
            estimated = max(1, len(text.split()) * 2)
            usage.input_tokens = estimated
            usage.total_tokens = estimated
            usage.raw_usage = {"usage_source": "estimated", "reason": "provider_response_missing_usage"}
        return usage
