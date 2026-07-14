"""Typed provider capability ports and transport-neutral request/response records."""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import ModelRequest, ModelResponse


class CapabilityRecord(BaseModel):
    """Strict capability DTO with camelCase API aliases."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)


class ProviderHttpRequest(CapabilityRecord):
    """Transport-neutral JSON request for one provider call."""

    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] = Field(alias="jsonBody")
    timeout_seconds: float = Field(default=60, alias="timeoutSeconds", gt=0, le=300)


class ProviderHttpResponse(CapabilityRecord):
    """Transport-neutral JSON response returned by a provider call."""

    status_code: int = Field(alias="statusCode", ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] = Field(alias="jsonBody")


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP boundary used by productive adapters and contract tests."""

    def __call__(self, request: ProviderHttpRequest) -> ProviderHttpResponse:
        """Execute one fail-closed HTTP request."""
        ...


class CapabilityUsage(CapabilityRecord):
    """Provider-reported token usage; missing fields remain unknown."""

    prompt_tokens: int | None = Field(default=None, alias="promptTokens", ge=0)
    total_tokens: int | None = Field(default=None, alias="totalTokens", ge=0)


class EmbeddingRequest(CapabilityRecord):
    """Typed request shared by documented NVIDIA embedding endpoints."""

    model: str = Field(min_length=1, max_length=256)
    input: str | list[str]
    input_type: Literal["query", "passage"] | None = Field(default=None, alias="inputType")
    encoding_format: Literal["float"] | None = Field(default=None, alias="encodingFormat")
    truncate: Literal["START", "END", "NONE"] | None = None

    @field_validator("model", "input_type", "encoding_format", "truncate")
    @classmethod
    def validate_named_value(cls, value: str | None) -> str | None:
        """Reject semantically empty model and option values."""
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str | list[str]) -> str | list[str]:
        """Reject empty or unbounded embedding inputs before transport."""
        values = [value] if isinstance(value, str) else value
        if len(values) > 1_000:
            raise ValueError("input must contain at most 1000 strings")
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("input must contain one or more non-empty strings")
        if any(len(item) > 200_000 for item in values):
            raise ValueError("input strings must contain at most 200000 characters")
        return value

    def provider_payload(self) -> dict[str, Any]:
        """Serialize only fields explicitly supported by the typed contract."""
        payload: dict[str, Any] = {"model": self.model, "input": self.input}
        if self.input_type is not None:
            payload["input_type"] = self.input_type
        if self.encoding_format is not None:
            payload["encoding_format"] = self.encoding_format
        if self.truncate is not None:
            payload["truncate"] = self.truncate
        return payload


class EmbeddingVector(CapabilityRecord):
    """One indexed vector returned by an embedding endpoint."""

    index: int = Field(ge=0)
    embedding: list[float]
    object: str = "embedding"

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: list[float]) -> list[float]:
        """Reject empty or non-finite vectors from provider responses."""
        if not value or any(not math.isfinite(item) for item in value):
            raise ValueError("embedding must contain finite numeric values")
        return value


class EmbeddingResponse(CapabilityRecord):
    """Normalized embedding response with honest optional usage."""

    provider_id: str = Field(alias="providerId")
    model: str
    data: list[EmbeddingVector]
    usage: CapabilityUsage | None = None


class RerankPassage(CapabilityRecord):
    """One passage submitted to or returned by a rerank endpoint."""

    text: str = Field(min_length=1, max_length=200_000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Reject semantically empty passages."""
        if not value.strip():
            raise ValueError("passage text must not be blank")
        return value


class RerankRequest(CapabilityRecord):
    """Typed request shared by documented NVIDIA reranking endpoints."""

    model: str = Field(min_length=1, max_length=256)
    query: str = Field(min_length=1, max_length=200_000)
    passages: list[RerankPassage] = Field(min_length=1, max_length=1_000)
    truncate: Literal["START", "END", "NONE"] | None = None

    @field_validator("model", "query", "truncate")
    @classmethod
    def validate_named_value(cls, value: str | None) -> str | None:
        """Reject semantically empty model, query, and option values."""
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    def provider_payload(self) -> dict[str, Any]:
        """Serialize the NVIDIA query/passage object contract."""
        payload: dict[str, Any] = {
            "model": self.model,
            "query": {"text": self.query},
            "passages": [{"text": passage.text} for passage in self.passages],
        }
        if self.truncate is not None:
            payload["truncate"] = self.truncate
        return payload


class RerankResult(CapabilityRecord):
    """One normalized ranking result."""

    index: int = Field(ge=0)
    relevance_score: float = Field(alias="relevanceScore")
    passage: RerankPassage | None = None

    @field_validator("relevance_score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        """Reject non-finite provider scores."""
        if not math.isfinite(value):
            raise ValueError("relevance score must be finite")
        return value


class RerankResponse(CapabilityRecord):
    """Normalized rerank response with honest optional usage."""

    provider_id: str = Field(alias="providerId")
    model: str
    rankings: list[RerankResult]
    usage: CapabilityUsage | None = None


class ImageGenerationRequest(CapabilityRecord):
    """Bounded common request for one documented image-generation adapter profile."""

    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=10_000)
    negative_prompt: str | None = Field(default=None, alias="negativePrompt", max_length=5_000)
    width: int | None = Field(default=None, ge=64, le=2_048)
    height: int | None = Field(default=None, ge=64, le=2_048)
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg_scale: float | None = Field(default=None, alias="cfgScale", ge=0, le=20)
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"] | None = Field(
        default=None,
        alias="aspectRatio",
    )

    @field_validator("project_id", "model", "prompt", "negative_prompt")
    @classmethod
    def validate_named_value(cls, value: str | None) -> str | None:
        """Reject semantically empty generation values."""
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_dimensions(self) -> ImageGenerationRequest:
        """Require width and height together so adapters never invent the missing dimension."""
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be provided together")
        return self


class ImageArtifactReference(CapabilityRecord):
    """Safe public reference to a durable generated image, never its local path or bytes."""

    artifact_id: str = Field(alias="artifactId", min_length=1, max_length=160)
    mime_type: Literal["image/png", "image/jpeg"] = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes", gt=0)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    download_path: str = Field(alias="downloadPath", min_length=1, max_length=512)


class ImageGenerationResponse(CapabilityRecord):
    """Image response referencing durable artifacts instead of raw payloads."""

    provider_id: str = Field(alias="providerId")
    model: str
    artifacts: list[ImageArtifactReference] = Field(min_length=1, max_length=1)


class ImageEditingRequest(CapabilityRecord):
    """Bounded image-edit request tied to an artifact in the same project."""

    project_id: str = Field(alias="projectId", min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(min_length=1, max_length=10_000)
    input_artifact_id: str = Field(alias="inputArtifactId", min_length=1)
    negative_prompt: str | None = Field(default=None, alias="negativePrompt", max_length=5_000)
    width: int | None = Field(default=None, ge=64, le=2_048)
    height: int | None = Field(default=None, ge=64, le=2_048)
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg_scale: float | None = Field(default=None, alias="cfgScale", ge=0, le=20)
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"] | None = Field(
        default=None,
        alias="aspectRatio",
    )

    @field_validator("project_id", "model", "prompt", "input_artifact_id", "negative_prompt")
    @classmethod
    def validate_named_value(cls, value: str | None) -> str | None:
        """Reject semantically empty editing values."""
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_dimensions(self) -> ImageEditingRequest:
        """Require width and height together so adapters never invent the missing dimension."""
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be provided together")
        return self


class ImageEditingResponse(ImageGenerationResponse):
    """Image-edit response referencing durable artifacts."""


@runtime_checkable
class ImageArtifactStore(Protocol):
    """Artifact boundary used by visual adapters without exposing filesystem paths."""

    def require_project(self, *, project_id: str) -> None:
        """Validate the output project before any provider call is made."""
        ...

    def read_image_data_url(self, *, project_id: str, artifact_id: str) -> str:
        """Load one validated, project-owned image as an internal provider data URL."""
        ...

    def persist_image(
        self,
        *,
        project_id: str,
        provider_id: str,
        model: str,
        content: bytes,
    ) -> ImageArtifactReference:
        """Validate and persist one provider image, returning only a safe reference."""
        ...


@runtime_checkable
class ChatCompletionProvider(Protocol):
    """Runtime-checkable chat-completion provider port."""

    provider_id: str

    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Execute one typed chat completion."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Runtime-checkable embedding provider port."""

    provider_id: str

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Execute one typed embedding request."""
        ...


@runtime_checkable
class RerankProvider(Protocol):
    """Runtime-checkable rerank provider port."""

    provider_id: str

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Execute one typed rerank request."""
        ...


@runtime_checkable
class ImageGenerationProvider(Protocol):
    """Runtime-checkable image-generation provider port."""

    provider_id: str

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResponse:
        """Generate and persist one image artifact."""
        ...


@runtime_checkable
class ImageEditingProvider(Protocol):
    """Runtime-checkable image-editing provider port."""

    provider_id: str

    def edit_image(self, request: ImageEditingRequest) -> ImageEditingResponse:
        """Edit an input artifact and persist the resulting artifacts."""
        ...
