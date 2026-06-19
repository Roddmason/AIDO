"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ProviderHealth(BaseModel):
    provider_id: str = Field(alias="providerId")
    status: str
    health_status: str = Field(alias="healthStatus")
    message: str = ""
    last_error: str | None = Field(default=None, alias="lastError")


class ModelInfo(BaseModel):
    provider_id: str = Field(alias="providerId")
    model: str
    display_name: str = Field(alias="displayName")
    context_window: int = Field(default=0, alias="contextWindow")
    max_output_tokens: int = Field(default=0, alias="maxOutputTokens")
    supports_tools: bool = Field(default=False, alias="supportsTools")
    supports_json: bool = Field(default=False, alias="supportsJson")
    supports_streaming: bool = Field(default=False, alias="supportsStreaming")
    supports_vision: bool = Field(default=False, alias="supportsVision")
    supports_reasoning: bool = Field(default=False, alias="supportsReasoning")
    free_tier: bool = Field(default=False, alias="freeTier")
    source: str = "provider"


class ModelRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageRecord(BaseModel):
    input_tokens: int = Field(default=0, alias="inputTokens")
    cached_input_tokens: int = Field(default=0, alias="cachedInputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    reasoning_tokens: int = Field(default=0, alias="reasoningTokens")
    tool_tokens: int = Field(default=0, alias="toolTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")
    raw_usage: dict[str, Any] = Field(default_factory=dict, alias="rawUsage")


class CostEstimate(BaseModel):
    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    currency: str = "USD"
    source: str = "unknown"


class ModelResponse(BaseModel):
    provider_id: str = Field(alias="providerId")
    model: str
    content: str
    usage: UsageRecord
    raw_response: Any = Field(default_factory=dict, alias="rawResponse")


class ModelProvider(ABC):
    provider_id: str

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    @abstractmethod
    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        raise NotImplementedError

    @abstractmethod
    def parse_usage(self, raw_response: Any) -> UsageRecord:
        raise NotImplementedError
