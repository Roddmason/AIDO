from __future__ import annotations

import os

from .openai_compatible import OpenAICompatibleProvider


class OpenAIAPIProvider(OpenAICompatibleProvider):
    def __init__(self, *, base_url: str | None = None, credential_ref: str = "OPENAI_API_KEY"):
        super().__init__(
            provider_id="openai_api",
            base_url=base_url or os.environ.get("AIDO_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            credential_ref=credential_ref,
        )
