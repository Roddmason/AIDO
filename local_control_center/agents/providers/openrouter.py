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

    def __init__(self, *, base_url: str | None = None, credential_ref: str | None = None):
        super().__init__(
            provider_id="openrouter",
            base_url=base_url
            or os.environ.get("AIDO_OPENROUTER_BASE_URL")
            or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            credential_ref=credential_ref,
        )
