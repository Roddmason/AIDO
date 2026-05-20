from __future__ import annotations

import os

from .openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, *, mock: bool = False):
        super().__init__(
            provider_id="openrouter",
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            credential_ref="OPENROUTER_API_KEY",
            mock=mock,
        )
