from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration
from local_control_center.shared.redaction import redact_secrets

from .base import CostEstimate, ModelInfo, ModelProvider, ModelRequest, ModelResponse, ProviderHealth, UsageRecord


def real_provider_calls_enabled() -> bool:
    return os.environ.get("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false").lower() == "true"


class OpenAICompatibleProvider(ModelProvider):
    def __init__(
        self,
        *,
        provider_id: str = "openai_compatible",
        base_url: str | None = None,
        credential_ref: str | None = None,
    ):
        runtime_configuration = runtime_provider_configuration(provider_id)
        self.provider_id = provider_id
        self.base_url = (
            base_url
            or (runtime_configuration.value("baseUrl") if runtime_configuration else None)
            or os.environ.get("AIDO_OPENAI_COMPATIBLE_BASE_URL")
            or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
            or ""
        ).rstrip("/")
        self.credential_ref = (
            credential_ref
            or (runtime_configuration.configured_env_ref("apiKey") if runtime_configuration else None)
            or ""
        )
        self.credential_resolver = CredentialResolver()

    def _credential(self) -> str:
        return self.credential_resolver.resolve(self.credential_ref).value or ""

    def health_check(self) -> ProviderHealth:
        if not self.base_url:
            return ProviderHealth(providerId=self.provider_id, status="misconfigured", healthStatus="misconfigured", message="Base URL is not configured")
        credential = self.credential_resolver.resolve(self.credential_ref, fetch=False)
        if credential.status == "invalid":
            return ProviderHealth(providerId=self.provider_id, status="misconfigured", healthStatus="misconfigured", message=credential.message)
        if credential.status in {"missing", "unsupported", "unknown"}:
            return ProviderHealth(providerId=self.provider_id, status="misconfigured", healthStatus="misconfigured", message=f"Credential ref {self.credential_ref} is {credential.status}")
        if not real_provider_calls_enabled():
            return ProviderHealth(providerId=self.provider_id, status="disabled", healthStatus="unknown", message="Real provider calls are disabled")
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self._credential()}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return ProviderHealth(
                providerId=self.provider_id,
                status="not_available",
                healthStatus="offline",
                message=f"Provider /models health check failed: {error.__class__.__name__}",
            )
        return ProviderHealth(providerId=self.provider_id, status="available", healthStatus="healthy", message="Provider /models responded")

    def list_models(self) -> list[ModelInfo]:
        if not real_provider_calls_enabled():
            raise RuntimeError("Real provider discovery is disabled by AIDO_ENABLE_REAL_PROVIDER_CALLS=false")
        if not self.base_url:
            return []
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self._credential()}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
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

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        if not real_provider_calls_enabled():
            raise RuntimeError("Real provider calls are disabled by AIDO_ENABLE_REAL_PROVIDER_CALLS=false")
        if not self.base_url or not self._credential():
            raise RuntimeError("Provider is missing base_url or credential_ref")
        payload = json.dumps(request.model_dump(by_alias=True, exclude_none=True)).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._credential()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = ""
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = str((message or {}).get("content") or "")
        return ModelResponse(
            providerId=self.provider_id,
            model=request.model,
            content=content,
            usage=self.parse_usage(raw),
            rawResponse=redact_secrets(raw),
        )

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        token_estimate = sum(len(str(message.get("content", "")).split()) for message in request.messages) * 2
        return CostEstimate(estimatedCostUsd=None, source=f"unknown:{self.provider_id}:{model}:{token_estimate}")

    def parse_usage(self, raw_response: Any) -> UsageRecord:
        usage = raw_response.get("usage", {}) if isinstance(raw_response, dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        reasoning_tokens = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
        cached_input_tokens = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or usage.get("cached_input_tokens") or 0)
        tool_tokens = int(usage.get("tool_tokens") or 0)
        total = int(usage.get("total_tokens") or input_tokens + output_tokens + reasoning_tokens + tool_tokens)
        return UsageRecord(
            inputTokens=input_tokens,
            cachedInputTokens=cached_input_tokens,
            outputTokens=output_tokens,
            reasoningTokens=reasoning_tokens,
            toolTokens=tool_tokens,
            totalTokens=total,
            rawUsage=redact_secrets(usage),
        )
