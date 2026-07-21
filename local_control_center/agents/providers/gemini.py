"""Conecta Google Gemini mediante su endpoint oficial compatible con OpenAI.

El adaptador conserva el transporte ``urllib`` comun y no incorpora el SDK de Google.
Autentica con ``Authorization: Bearer`` y solo resuelve las variables especificas de
Gemini; ``GOOGLE_API_KEY`` no se inspecciona ni se usa como fallback implicito.

El millon de tokens publicado por Google es una ventana de contexto por solicitud, no
una bolsa fija de cuota gratuita. Los limites del tier gratuito son dinamicos por
proyecto y modelo, por lo que este modulo declara elegibilidad sin inventar cupos.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.agents.provider_catalog import GEMINI_MODEL_MANIFEST
from local_control_center.agents.runtime_provider_config import runtime_provider_configuration

from .base import ModelInfo, UsageRecord
from .openai_compatible import OpenAICompatibleProvider

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_CONTEXT_WINDOW = 1_048_576
GEMINI_MAX_OUTPUT_TOKENS = 65_536


class GeminiProvider(OpenAICompatibleProvider):
    """Proveedor Gemini con identidad, credenciales y metadatos propios."""

    def __init__(
        self,
        *,
        provider_id: str = "gemini",
        base_url: str | None = None,
        credential_ref: str | None = None,
    ):
        canonical_provider = provider_id == "gemini"
        runtime_configuration = (
            runtime_provider_configuration("gemini")
            if canonical_provider and credential_ref is None
            else None
        )
        resolved_credential_ref = (
            credential_ref
            if credential_ref is not None
            else (
                runtime_configuration.configured_env_ref("apiKey")
                if runtime_configuration is not None
                else ""
            )
        )
        resolved_base_url = (
            base_url if base_url is not None else GEMINI_OPENAI_BASE_URL if canonical_provider else ""
        )
        super().__init__(
            provider_id=provider_id,
            base_url=resolved_base_url,
            credential_ref=resolved_credential_ref,
            use_legacy_fallbacks=False,
        )

    def list_models(self) -> list[ModelInfo]:
        """Descubre modelos y enriquece solo los modelos Standard Free conocidos."""
        return [self._with_official_metadata(model) for model in super().list_models()]

    def parse_usage(self, raw_response: Any) -> UsageRecord:
        """Normaliza los contadores OpenAI estandar reportados por Gemini."""
        return super().parse_usage(raw_response)

    @staticmethod
    def _with_official_metadata(model: ModelInfo) -> ModelInfo:
        """Aplica capacidades oficiales sin inferir elegibilidad para modelos desconocidos."""
        normalized_id = model.model.removeprefix("models/")
        metadata = GEMINI_MODEL_MANIFEST.get(normalized_id)
        if metadata is None:
            return model
        return model.model_copy(
            update={
                "model": normalized_id,
                "display_name": str(metadata["displayName"]),
                "context_window": int(metadata["contextWindow"]),
                "max_output_tokens": int(metadata["maxOutputTokens"]),
                "supports_tools": bool(metadata["supportsTools"]),
                "supports_json": bool(metadata["supportsJson"]),
                "supports_streaming": bool(metadata["supportsStreaming"]),
                "supports_vision": bool(metadata["supportsVision"]),
                "supports_reasoning": bool(metadata["supportsReasoning"]),
                "free_tier": bool(metadata["freeTier"]),
            }
        )
