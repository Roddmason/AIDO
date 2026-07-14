"""Enruta peticiones al agregador OpenRouter usando su API compatible con OpenAI.

OpenRouter expone modelos de multiples proveedores tras un unico endpoint estilo
OpenAI, asi que esta clase solo aporta su URL base y deja el resto del comportamiento
(HTTP, descubrimiento, uso, costo) en `OpenAICompatibleProvider`.

@author Rodrigo Mason
"""

from __future__ import annotations

import os

from .openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """Proveedor para el agregador OpenRouter; solo define su URL base sobre la base compatible."""

    def __init__(
        self,
        *,
        provider_id: str = "openrouter",
        base_url: str | None = None,
        credential_ref: str | None = None,
    ):
        is_legacy_provider = provider_id == "openrouter"
        resolved_base_url = (
            base_url
            if base_url is not None
            else (
                os.environ.get("AIDO_OPENROUTER_BASE_URL")
                or os.environ.get("OPENROUTER_BASE_URL")
                or "https://openrouter.ai/api/v1"
                if is_legacy_provider
                else ""
            )
        )
        super().__init__(
            provider_id=provider_id,
            base_url=resolved_base_url,
            credential_ref=(
                credential_ref
                if credential_ref is not None
                else None if is_legacy_provider else ""
            ),
            use_legacy_fallbacks=is_legacy_provider,
        )
        if not is_legacy_provider:
            self.base_url = resolved_base_url.rstrip("/")
            self.credential_ref = credential_ref or ""
