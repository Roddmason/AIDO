from __future__ import annotations

import os

from .openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, *, base_url: str | None = None, credential_ref: str = "OPENROUTER_API_KEY"):
        super().__init__(
            provider_id="openrouter",
            base_url=base_url or os.environ.get("AIDO_OPENROUTER_BASE_URL") or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            credential_ref=credential_ref,
        )
