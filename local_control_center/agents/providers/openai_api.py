"""Conecta con la API oficial de OpenAI reutilizando el proveedor compatible base.

Solo fija el `provider_id`, la URL por defecto de OpenAI (override via AIDO/OPENAI
base url) y la referencia de credencial; toda la logica HTTP, de costo y de uso la
hereda de `OpenAICompatibleProvider`.
"""

from __future__ import annotations

import os

from .openai_compatible import OpenAICompatibleProvider


class OpenAIAPIProvider(OpenAICompatibleProvider):
    """Proveedor para la API oficial de OpenAI; configura URL y credencial sobre la base compatible."""

    def __init__(self, *, base_url: str | None = None, credential_ref: str = "OPENAI_API_KEY"):
        super().__init__(
            provider_id="openai_api",
            base_url=base_url
            or os.environ.get("AIDO_OPENAI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            credential_ref=credential_ref,
        )
