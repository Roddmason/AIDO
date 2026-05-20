from __future__ import annotations

import os

from .openai_compatible import OpenAICompatibleProvider


class OpenAIAPIProvider(OpenAICompatibleProvider):
    def __init__(self, *, mock: bool = False):
        super().__init__(
            provider_id="openai_api",
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            credential_ref="OPENAI_API_KEY",
            mock=mock,
        )
