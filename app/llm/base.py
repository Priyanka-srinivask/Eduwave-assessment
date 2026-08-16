"""Provider-agnostic contract for talking to a language model.

Every concrete provider (mock, Gemini, …) returns the same `LLMResult`, so the
orchestrator never needs provider-specific code. Token usage is part of the
result because observability (cost, tokens) is a first-class requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(Exception):
    """Base class for all provider failures.

    The orchestrator catches this one type and turns it into a controlled
    fallback response, so no provider-specific exception ever reaches the user.
    """


class ProviderTimeout(ProviderError):
    """The model call exceeded the configured timeout."""


class ProviderRateLimited(ProviderError):
    """The provider returned a rate-limit / quota error (HTTP 429 etc.)."""


@dataclass
class LLMResult:
    """Uniform result from any provider."""
    text: str                       # raw text the model returned (expected JSON)
    model: str                      # concrete model id used
    provider: str                   # "mock" | "gemini" | ...
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Providers may attach extra diagnostics without breaking the contract.
    raw_meta: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


class LLMProvider(Protocol):
    """Structural interface. Any object with a matching `generate` is a provider.

    Using a Protocol (structural typing) rather than an ABC keeps the mock and
    real providers decoupled — they don't need to inherit from a shared base,
    they just need to match this shape.
    """

    name: str

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Call the model once and return its raw output + usage.

        Implementations must translate provider-specific failures into
        `ProviderError` (or a subclass). They should NOT retry internally —
        retry/backoff is handled one level up so the policy is uniform.
        """
        ...
