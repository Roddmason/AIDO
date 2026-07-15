"""Expone un proxy/gateway LiteLLM a traves de su superficie compatible con OpenAI.

LiteLLM unifica muchos backends tras una API estilo OpenAI; este adaptador hereda toda
la mecanica de `OpenAICompatibleProvider` y solo distingue el proveedor por su id y la
deteccion opcional del paquete `litellm`.

@author Rodrigo Mason
"""

from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class LiteLLMAdapter(OpenAICompatibleProvider):
    """Adaptador para un gateway LiteLLM expuesto con API estilo OpenAI."""

    def __init__(self, *, base_url: str | None = None, credential_ref: str | None = None):
        super().__init__(
            provider_id="litellm",
            base_url=base_url,
            credential_ref=credential_ref,
            credential_required=False,
        )

    @staticmethod
    def available() -> bool:
        """Indica si el SDK `litellm` esta instalado en el entorno."""
        try:
            import litellm  # noqa: F401
        except ImportError:
            return False
        return True
