"""Integra los endpoints NIM de NVIDIA, compatibles con OpenAI, con manejo propio de cuota.

Reutiliza el transporte estilo OpenAI pero endurece la lectura de uso (solo confia en
`usage` si el proveedor realmente lo devuelve) y traduce los 429 en cuota: registra el
rate limit en `QuotaManager` con un retry-after fijo cuando hay conexion a la base.

@author Rodrigo Mason
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration
from local_control_center.evidence.image_validation import MAX_IMAGE_BYTES
from local_control_center.shared.redaction import redact_secrets

from .base import CostEstimate, ModelInfo, ModelRequest, ModelResponse, ProviderHealth, UsageRecord
from .capabilities import (
    CapabilityUsage,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingVector,
    HttpTransport,
    ImageArtifactStore,
    ImageEditingRequest,
    ImageEditingResponse,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ProviderHttpRequest,
    ProviderHttpResponse,
    RerankPassage,
    RerankRequest,
    RerankResponse,
    RerankResult,
)
from .http_transport import urlopen_fail_closed
from .image_artifacts import ImageArtifactError, inspect_image
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
MAX_PROVIDER_JSON_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BASE64_CHARS = ((MAX_IMAGE_BYTES + 2) // 3) * 4


def _provider_returned_usage(raw_response: Any) -> bool:
    if not isinstance(raw_response, dict):
        return False
    usage = raw_response.get("usage")
    return isinstance(usage, dict) and any(usage.get(key) is not None for key in USAGE_TOKEN_KEYS)


class NvidiaNimCapabilityError(RuntimeError):
    """Stable pre-network or redacted transport error for a NVIDIA capability call."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _stdlib_transport(request: ProviderHttpRequest) -> ProviderHttpResponse:
    encoded = json.dumps(request.json_body, separators=(",", ":")).encode("utf-8")
    http_request = urllib.request.Request(
        request.url,
        data=None if request.method == "GET" else encoded,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urlopen_fail_closed(http_request, timeout=request.timeout_seconds) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_PROVIDER_JSON_BYTES:
                        raise NvidiaNimCapabilityError("provider_response_too_large")
                except ValueError:
                    pass
            raw_response = response.read(MAX_PROVIDER_JSON_BYTES + 1)
            if len(raw_response) > MAX_PROVIDER_JSON_BYTES:
                raise NvidiaNimCapabilityError("provider_response_too_large")
            payload = json.loads(raw_response.decode("utf-8"))
            status_code = int(getattr(response, "status", 200))
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        raw_response = error.read(MAX_PROVIDER_JSON_BYTES + 1)
        if len(raw_response) <= MAX_PROVIDER_JSON_BYTES:
            try:
                decoded = json.loads(raw_response.decode("utf-8"))
                payload = decoded if isinstance(decoded, dict) else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
        else:
            payload = {}
        status_code = int(error.code)
        headers = {str(key).lower(): str(value) for key, value in error.headers.items()}
    except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise NvidiaNimCapabilityError("provider_request_failed") from error
    if not isinstance(payload, dict):
        raise NvidiaNimCapabilityError("provider_response_invalid")
    return ProviderHttpResponse(statusCode=status_code, headers=headers, jsonBody=payload)


class NvidiaNimProvider(OpenAICompatibleProvider):
    """Proveedor para los endpoints NIM de NVIDIA con registro de rate limit en la cuota."""

    def __init__(
        self,
        *,
        provider_id: str = "nvidia_nim",
        connection: sqlite3.Connection | None = None,
        base_url: str | None = None,
        credential_ref: str | None = None,
        deployment_mode: str = "hosted_trial",
        api_family: str = "chat_completions",
        adapter_profile: str = "auto",
        terms_mode: str = "evaluation",
        pricing_mode: str = "unknown",
        transport: HttpTransport | None = None,
    ):
        is_legacy_provider = provider_id == "nvidia_nim"
        runtime_configuration = runtime_provider_configuration(provider_id) if is_legacy_provider else None
        resolved_base_url = (
            base_url
            if base_url is not None
            else (
                (runtime_configuration.value("baseUrl") if runtime_configuration else None)
                or (
                    "https://integrate.api.nvidia.com/v1"
                    if is_legacy_provider or deployment_mode == "hosted_trial"
                    else ""
                )
            )
        )
        resolved_credential_ref = (
            credential_ref
            if credential_ref is not None
            else (
                (runtime_configuration.configured_env_ref("apiKey") if runtime_configuration else None)
                or ("NVIDIA_NIM_API_KEY" if is_legacy_provider else "")
            )
        )
        super().__init__(
            provider_id=provider_id,
            base_url=resolved_base_url,
            credential_ref=resolved_credential_ref,
            credential_required=not deployment_mode.startswith("self_hosted"),
            use_legacy_fallbacks=False,
        )
        # The generic base class has legacy env fallbacks for falsy values. Endpoint-scoped
        # NVIDIA accounts must retain their persisted empty state instead of inheriting them.
        self.base_url = resolved_base_url.rstrip("/")
        self.credential_ref = resolved_credential_ref
        self.connection = connection
        self.deployment_mode = deployment_mode
        self.api_family = api_family
        self.adapter_profile = adapter_profile
        self.terms_mode = terms_mode
        self.pricing_mode = pricing_mode
        self.transport: HttpTransport = transport or _stdlib_transport

    def _assert_capability(self, expected: str) -> None:
        if self.api_family != expected:
            raise NvidiaNimCapabilityError("unsupported_api_family")
        allowed_profiles = {
            "chat_completions": {"auto", "nvidia_openai_chat"},
            "embeddings": {"auto", "nvidia_openai_embeddings"},
            "rerank": (
                {"auto", "nvidia_nim_ranking"}
                if self.deployment_mode.startswith("self_hosted")
                else {"auto", "nvidia_hosted_rerank"}
            ),
        }
        if expected in allowed_profiles and self.adapter_profile not in allowed_profiles[expected]:
            raise NvidiaNimCapabilityError("unsupported_adapter_profile")

    def _capability_headers(self) -> dict[str, str]:
        credential = self._credential() if self.credential_ref else ""
        if (self.credential_ref or self.credential_required) and not credential:
            raise NvidiaNimCapabilityError("credential_missing")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return headers

    def _request_capability(
        self,
        *,
        method: str,
        suffix: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one bounded JSON capability request through the injected transport."""
        response = self.transport(
            ProviderHttpRequest(
                method=method,
                url=f"{self.base_url}{suffix}",
                headers=self._capability_headers(),
                jsonBody=payload or {},
                timeoutSeconds=60,
            )
        )
        if response.status_code == 429:
            if self.connection is not None:
                model = str((payload or {}).get("model") or "*")
                try:
                    QuotaManager(self.connection).record_rate_limit(
                        provider_id=self.provider_id,
                        model=model,
                        headers=response.headers,
                        error_class="NvidiaNimRateLimit",
                    )
                except sqlite3.Error as error:
                    raise NvidiaNimCapabilityError("provider_rate_limit_persistence_failed") from error
            raise NvidiaNimCapabilityError("provider_rate_limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise NvidiaNimCapabilityError("provider_request_failed")
        return response.json_body

    def _post_capability(self, *, suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one bounded JSON POST capability request."""
        return self._request_capability(method="POST", suffix=suffix, payload=payload)

    @staticmethod
    def _capability_usage(payload: dict[str, Any]) -> CapabilityUsage | None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt_tokens = usage.get("prompt_tokens")
        total_tokens = usage.get("total_tokens")
        if prompt_tokens is None and total_tokens is None:
            return None
        return CapabilityUsage(promptTokens=prompt_tokens, totalTokens=total_tokens)

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Execute the documented OpenAI-compatible NVIDIA embeddings contract."""
        self._assert_capability("embeddings")
        payload = self._post_capability(suffix="/embeddings", payload=request.provider_payload())
        try:
            data = [EmbeddingVector.model_validate(item) for item in payload.get("data", [])]
            if not data:
                raise ValueError("missing embedding data")
            input_count = 1 if isinstance(request.input, str) else len(request.input)
            indices = [item.index for item in data]
            if sorted(indices) != list(range(input_count)):
                raise ValueError("embedding indices do not match request input")
            return EmbeddingResponse(
                providerId=self.provider_id,
                model=str(payload.get("model") or request.model),
                data=data,
                usage=self._capability_usage(payload),
            )
        except (TypeError, ValueError) as error:
            raise NvidiaNimCapabilityError("provider_response_invalid") from error

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Execute NVIDIA chat through the same injected, deployment-aware transport port."""
        self._assert_capability("chat_completions")
        request_payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": request.stream,
        }
        if request.temperature is not None:
            request_payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            request_payload["max_tokens"] = request.max_tokens
        payload = self._post_capability(
            suffix="/chat/completions",
            payload=request_payload,
        )
        choices = payload.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise NvidiaNimCapabilityError("provider_response_invalid")
        return ModelResponse(
            providerId=self.provider_id,
            model=str(payload.get("model") or request.model),
            content=content,
            usage=self.parse_usage(payload),
            rawResponse=redact_secrets(payload),
        )

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Execute hosted `/reranking` or self-hosted `/ranking` by explicit profile."""
        self._assert_capability("rerank")
        suffix = "/ranking" if self.deployment_mode.startswith("self_hosted") else "/reranking"
        payload = self._post_capability(suffix=suffix, payload=request.provider_payload())
        raw_rankings = payload.get("rankings")
        if not isinstance(raw_rankings, list) or not raw_rankings:
            raise NvidiaNimCapabilityError("provider_response_invalid")
        rankings: list[RerankResult] = []
        seen_indices: set[int] = set()
        try:
            for item in raw_rankings:
                if not isinstance(item, dict):
                    raise ValueError("ranking must be an object")
                score = item.get("logit", item.get("relevance_score", item.get("score")))
                index = item["index"]
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or index >= len(request.passages)
                    or index in seen_indices
                ):
                    raise ValueError("ranking index does not match request passages")
                seen_indices.add(index)
                passage = item.get("passage")
                rankings.append(
                    RerankResult(
                        index=index,
                        relevanceScore=score,
                        passage=RerankPassage.model_validate(passage) if isinstance(passage, dict) else None,
                    )
                )
            if seen_indices != set(range(len(request.passages))):
                raise ValueError("ranking indices do not cover request passages")
            return RerankResponse(
                providerId=self.provider_id,
                model=str(payload.get("model") or request.model),
                rankings=rankings,
                usage=self._capability_usage(payload),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise NvidiaNimCapabilityError("provider_response_invalid") from error

    def list_models(self) -> list[ModelInfo]:
        """Discover models only through a documented, bounded NVIDIA list contract."""
        if self.api_family in {"image_generation", "image_editing"}:
            raise NvidiaNimCapabilityError("explicit_model_manifest_required")
        if self.api_family == "rerank" and not self.deployment_mode.startswith("self_hosted"):
            raise NvidiaNimCapabilityError("explicit_model_manifest_required")
        self._assert_capability(self.api_family)
        payload = self._request_capability(method="GET", suffix="/models")
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise NvidiaNimCapabilityError("provider_response_invalid")
        models: list[ModelInfo] = []
        try:
            for item in raw_models:
                if not isinstance(item, dict):
                    raise ValueError("model entry must be an object")
                model = item.get("id") or item.get("model")
                if not isinstance(model, str) or not model.strip():
                    raise ValueError("model id is required")
                models.append(
                    ModelInfo(
                        providerId=self.provider_id,
                        model=model,
                        displayName=model,
                        source="provider",
                    )
                )
        except (TypeError, ValueError) as error:
            raise NvidiaNimCapabilityError("provider_response_invalid") from error
        return models

    def health_check(self) -> ProviderHealth:
        """Probe only documented passive endpoints for the configured deployment contract."""
        if self.api_family in {"image_generation", "image_editing"} and not self.deployment_mode.startswith(
            "self_hosted"
        ):
            return ProviderHealth(
                providerId=self.provider_id,
                status="configured",
                healthStatus="unknown",
                message="passive_health_endpoint_not_documented_for_hosted_visual",
            )
        if self.api_family == "rerank" and not self.deployment_mode.startswith("self_hosted"):
            return ProviderHealth(
                providerId=self.provider_id,
                status="configured",
                healthStatus="unknown",
                message="passive_health_endpoint_not_documented_for_hosted_rerank",
            )
        if not self.base_url:
            return ProviderHealth(
                providerId=self.provider_id,
                status="misconfigured",
                healthStatus="misconfigured",
                message="Provider base URL is not configured",
            )
        try:
            if self.deployment_mode.startswith("self_hosted"):
                payload = self._request_capability(method="GET", suffix="/health/ready")
                if payload.get("ready") is not True:
                    raise NvidiaNimCapabilityError("provider_not_ready")
                message = "Provider /health/ready responded ready"
            else:
                self.list_models()
                message = "Provider /models responded"
        except NvidiaNimCapabilityError as error:
            return ProviderHealth(
                providerId=self.provider_id,
                status="not_available",
                healthStatus="offline",
                message=error.code,
            )
        return ProviderHealth(
            providerId=self.provider_id,
            status="available",
            healthStatus="healthy",
            message=message,
        )

    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        """No hay tarifa publicada para NIM: devuelve costo desconocido marcando proveedor y modelo."""
        return CostEstimate(estimatedCostUsd=None, source=f"unknown:{self.provider_id}:{model}")

    def handle_error(self, *, status_code: int, message: str, model: str) -> ProviderHealth:
        """Traduce un error HTTP a salud; en 429 registra el rate limit en la cuota si hay conexion."""
        health = "degraded" if status_code == 429 else "offline"
        if status_code == 429 and self.connection is not None:
            QuotaManager(self.connection).record_rate_limit(
                provider_id=self.provider_id, model=model, retry_after_seconds=300
            )
        return ProviderHealth(
            providerId=self.provider_id,
            status="rate_limited" if status_code == 429 else "error",
            healthStatus=health,
            message=message,
        )

    def parse_usage(self, raw_response: Any):
        """Solo confia en el `usage` reportado por NIM; si falta, marca el origen como desconocido."""
        if not _provider_returned_usage(raw_response):
            return UsageRecord(
                rawUsage={"usage_source": "unknown", "reason": "provider_response_missing_usage"}
            )
        usage = super().parse_usage(raw_response)
        usage.raw_usage = {"usage_source": "provider", **usage.raw_usage}
        return usage


class NvidiaNimVisualProvider(NvidiaNimProvider):
    """Explicit visual NIM profiles that persist outputs through the artifact boundary."""

    def __init__(self, *, image_artifact_store: ImageArtifactStore | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.image_artifact_store = image_artifact_store

    def _artifact_store(self) -> ImageArtifactStore:
        if self.image_artifact_store is None:
            raise NvidiaNimCapabilityError("image_artifact_store_required")
        return self.image_artifact_store

    def _assert_visual_profile(self, expected: str) -> None:
        if self.api_family != expected:
            raise NvidiaNimCapabilityError("unsupported_api_family")
        self_hosted = self.deployment_mode.startswith("self_hosted")
        hosted_or_partner = self.deployment_mode in {"hosted_trial", "partner_paid"}
        allowed_profiles = {
            "image_generation": (
                {"nvidia_qwen_image_generation_infer", "nvidia_openai_image_generation"}
                if self_hosted
                else {"nvidia_hosted_prompt_image_generation"}
                if hosted_or_partner
                else set()
            ),
            "image_editing": (
                {"nvidia_qwen_image_editing_infer", "nvidia_openai_image_editing"} if self_hosted else set()
            ),
        }
        if self.adapter_profile not in allowed_profiles[expected]:
            raise NvidiaNimCapabilityError("unsupported_adapter_profile")

    @staticmethod
    def _common_options(request: ImageGenerationRequest | ImageEditingRequest) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if request.negative_prompt is not None:
            options["negative_prompt"] = request.negative_prompt
        if request.width is not None and request.height is not None:
            options["width"] = request.width
            options["height"] = request.height
        if request.seed is not None:
            options["seed"] = request.seed
        if request.steps is not None:
            options["steps"] = request.steps
        if request.cfg_scale is not None:
            options["cfg_scale"] = request.cfg_scale
        return options

    @staticmethod
    def _openai_options(request: ImageGenerationRequest | ImageEditingRequest) -> dict[str, Any]:
        if request.aspect_ratio is not None:
            raise NvidiaNimCapabilityError("unsupported_image_option")
        options = NvidiaNimVisualProvider._common_options(request)
        width = options.pop("width", None)
        height = options.pop("height", None)
        if width is not None and height is not None:
            options["size"] = f"{width}x{height}"
        return options

    @staticmethod
    def _decoded_image(payload: dict[str, Any], *, openai_compatible: bool) -> bytes:
        if openai_compatible:
            outputs = payload.get("data")
            raw_image = (
                outputs[0].get("b64_json")
                if isinstance(outputs, list) and len(outputs) == 1 and isinstance(outputs[0], dict)
                else None
            )
        else:
            outputs = payload.get("artifacts")
            artifact = (
                outputs[0]
                if isinstance(outputs, list) and len(outputs) == 1 and isinstance(outputs[0], dict)
                else None
            )
            if artifact is None:
                raw_image = None
            else:
                finish_reason = (
                    artifact.get("finishReason")
                    or artifact.get("finish_reason")
                    or payload.get("finishReason")
                    or payload.get("finish_reason")
                )
                if not isinstance(finish_reason, str) or finish_reason.upper() != "SUCCESS":
                    raise NvidiaNimCapabilityError("provider_response_invalid")
                raw_image = artifact.get("base64")
        if not isinstance(raw_image, str) or not raw_image or len(raw_image) > MAX_IMAGE_BASE64_CHARS:
            raise NvidiaNimCapabilityError("provider_response_invalid")
        try:
            content = base64.b64decode(raw_image, validate=True)
            inspect_image(content)
        except (binascii.Error, ValueError, ImageArtifactError) as error:
            raise NvidiaNimCapabilityError("provider_response_invalid") from error
        return content

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResponse:
        """Generate exactly one image and return a durable safe reference."""
        self._assert_visual_profile("image_generation")
        artifact_store = self._artifact_store()
        artifact_store.require_project(project_id=request.project_id)
        if self.adapter_profile == "nvidia_hosted_prompt_image_generation":
            endpoint_model = urlparse(self.base_url).path.removeprefix("/v1/genai/").rstrip("/")
            if endpoint_model != request.model:
                raise NvidiaNimCapabilityError("model_endpoint_mismatch")
            if any(
                value is not None
                for value in (
                    request.negative_prompt,
                    request.width,
                    request.height,
                    request.steps,
                    request.cfg_scale,
                    request.aspect_ratio,
                )
            ):
                raise NvidiaNimCapabilityError("unsupported_image_option")
            payload: dict[str, Any] = {"prompt": request.prompt}
            if request.seed is not None:
                payload["seed"] = request.seed
            suffix = ""
            openai_compatible = False
        elif self.adapter_profile == "nvidia_openai_image_generation":
            payload = {
                "model": request.model,
                "prompt": request.prompt,
                **self._openai_options(request),
                "n": 1,
                "response_format": "b64_json",
            }
            suffix = "/images/generations"
            openai_compatible = True
        else:
            if request.aspect_ratio is not None:
                raise NvidiaNimCapabilityError("unsupported_image_option")
            payload = {
                "prompt": request.prompt,
                **self._common_options(request),
                "samples": 1,
            }
            suffix = "/infer"
            openai_compatible = False
        response = self._post_capability(suffix=suffix, payload=payload)
        content = self._decoded_image(response, openai_compatible=openai_compatible)
        artifact = artifact_store.persist_image(
            project_id=request.project_id,
            provider_id=self.provider_id,
            model=request.model,
            content=content,
        )
        return ImageGenerationResponse(
            providerId=self.provider_id,
            model=request.model,
            artifacts=[artifact],
        )

    def edit_image(self, request: ImageEditingRequest) -> ImageEditingResponse:
        """Load one project-owned input image, edit it and persist exactly one output."""
        self._assert_visual_profile("image_editing")
        artifact_store = self._artifact_store()
        artifact_store.require_project(project_id=request.project_id)
        image = artifact_store.read_image_data_url(
            project_id=request.project_id,
            artifact_id=request.input_artifact_id,
        )
        if self.adapter_profile == "nvidia_openai_image_editing":
            payload: dict[str, Any] = {
                "model": request.model,
                "prompt": request.prompt,
                "image": image,
                **self._openai_options(request),
                "n": 1,
                "response_format": "b64_json",
            }
            suffix = "/images/edits"
            openai_compatible = True
        else:
            payload = {
                "prompt": request.prompt,
                "image": image,
                **self._common_options(request),
                "samples": 1,
            }
            if request.aspect_ratio is not None:
                payload["aspect_ratio"] = request.aspect_ratio
            suffix = "/infer"
            openai_compatible = False
        response = self._post_capability(suffix=suffix, payload=payload)
        content = self._decoded_image(response, openai_compatible=openai_compatible)
        artifact = artifact_store.persist_image(
            project_id=request.project_id,
            provider_id=self.provider_id,
            model=request.model,
            content=content,
        )
        return ImageEditingResponse(
            providerId=self.provider_id,
            model=request.model,
            artifacts=[artifact],
        )
