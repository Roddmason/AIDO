from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class LiteLLMAdapter(OpenAICompatibleProvider):
    def __init__(self, *, base_url: str | None = None, credential_ref: str = "LITELLM_API_KEY"):
        super().__init__(provider_id="litellm", base_url=base_url, credential_ref=credential_ref)

    @staticmethod
    def available() -> bool:
        try:
            import litellm  # noqa: F401
        except ImportError:
            return False
        return True
