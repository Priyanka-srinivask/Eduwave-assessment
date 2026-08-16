"""LLM provider abstraction.

`get_provider()` is the single factory the rest of the app calls; it hides which
concrete provider is active behind the `LLMProvider` protocol.
"""
from __future__ import annotations

from app.config import settings
from app.llm.base import LLMProvider, LLMResult, ProviderError
from app.llm.mock import MockProvider

__all__ = ["LLMProvider", "LLMResult", "ProviderError", "get_provider"]


def get_provider(name: str | None = None) -> LLMProvider:
    """Return the configured provider instance.

    Defaults to the value in settings (``mock`` unless overridden), so the app
    and tests run offline unless a real key/provider is explicitly configured.
    """
    provider_name = (name or settings.llm_provider).lower()

    if provider_name == "mock":
        return MockProvider()

    if provider_name == "gemini":
        # Imported lazily so the google SDK is only required when actually used.
        from app.llm.gemini import GeminiProvider

        return GeminiProvider()

    raise ProviderError(f"Unknown LLM provider: {provider_name!r}")
