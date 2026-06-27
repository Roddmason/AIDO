"""Define el contrato comun que todo proveedor LLM del slice debe implementar.

Declara los DTO de intercambio (peticion/respuesta, uso de tokens, costo, salud y
catalogo de modelos) y la clase base abstracta `ModelProvider`. Los DTO usan alias
camelCase para serializar hacia el frontend; las implementaciones concretas viven en
los demas modulos de este paquete y dependen solo de estos tipos, no entre si.

@author Rodrigo Mason
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ProviderHealth(BaseModel):
    """Estado operativo de un proveedor: disponibilidad, salud y, si falla, el motivo."""

    provider_id: str = Field(alias="providerId")
    status: str
    health_status: str = Field(alias="healthStatus")
    message: str = ""
    last_error: str | None = Field(default=None, alias="lastError")


class ModelInfo(BaseModel):
    """Modelo expuesto por un proveedor con sus capacidades declaradas (tools, vision, etc.)."""

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
    """Peticion de chat normalizada que se traduce al payload nativo de cada proveedor."""

    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageRecord(BaseModel):
    """Consumo de tokens desglosado; `raw_usage.usage_source` marca si vino del proveedor o se estimo."""

    input_tokens: int = Field(default=0, alias="inputTokens")
    cached_input_tokens: int = Field(default=0, alias="cachedInputTokens")
    output_tokens: int = Field(default=0, alias="outputTokens")
    reasoning_tokens: int = Field(default=0, alias="reasoningTokens")
    tool_tokens: int = Field(default=0, alias="toolTokens")
    total_tokens: int = Field(default=0, alias="totalTokens")
    raw_usage: dict[str, Any] = Field(default_factory=dict, alias="rawUsage")


class CostEstimate(BaseModel):
    """Costo estimado de una peticion; `None` cuando el proveedor no publica precios fiables."""

    estimated_cost_usd: float | None = Field(default=None, alias="estimatedCostUsd")
    currency: str = "USD"
    source: str = "unknown"


class ModelResponse(BaseModel):
    """Respuesta unificada de una completion: texto, uso y respuesta cruda ya redactada."""

    provider_id: str = Field(alias="providerId")
    model: str
    content: str
    usage: UsageRecord
    raw_response: Any = Field(default_factory=dict, alias="rawResponse")


class ModelProvider(ABC):
    """Contrato que abstrae un backend LLM tras una interfaz uniforme para el resto del sistema."""

    provider_id: str

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """Sondea el proveedor y reporta si esta disponible, mal configurado o caido."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Descubre los modelos que el proveedor ofrece para esta cuenta."""
        raise NotImplementedError

    @abstractmethod
    def chat_completion(self, request: ModelRequest) -> ModelResponse:
        """Ejecuta una completion de chat y devuelve la respuesta normalizada."""
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, request: ModelRequest, model: str) -> CostEstimate:
        """Estima el costo en USD de servir la peticion con el modelo dado."""
        raise NotImplementedError

    @abstractmethod
    def parse_usage(self, raw_response: Any) -> UsageRecord:
        """Extrae el consumo de tokens del payload crudo segun el esquema del proveedor."""
        raise NotImplementedError
