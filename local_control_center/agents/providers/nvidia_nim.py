from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration

from .base import CostEstimate, ModelInfo, ModelRequest, ProviderHealth, UsageRecord
from .openai_compatible import OpenAICompatibleProvider


USAGE_TOKEN_KEYS = {
    "prompt_tokens",
    "input_tokens",
    "completion_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
    "tool_tokens",
}


def _provider_returned_usage(raw_response: Any) -> bool:
    if not isinstance(raw_response, dict):
        return False
    usage = raw_response.get("usage")
    return isinstance(usage, dict) and any(usage.get(key) is not None for key in USAGE_TOKEN_KEYS)


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
        return CostEstimate(estimatedCostUsd=None, source=f"unknown:nvidia_nim:{model}")

    def handle_error(self, *, status_code: int, message: str, model: str) -> ProviderHealth:
        health = "degraded" if status_code == 429 else "offline"
        if status_code == 429 and self.connection is not None:
            QuotaManager(self.connection).record_rate_limit(provider_id=self.provider_id, model=model, retry_after_seconds=300)
        return ProviderHealth(providerId=self.provider_id, status="rate_limited" if status_code == 429 else "error", healthStatus=health, message=message)

    def parse_usage(self, raw_response: Any):
        if not _provider_returned_usage(raw_response):
            return UsageRecord(rawUsage={"usage_source": "unknown", "reason": "provider_response_missing_usage"})
        usage = super().parse_usage(raw_response)
        usage.raw_usage = {"usage_source": "provider", **usage.raw_usage}
        return usage
