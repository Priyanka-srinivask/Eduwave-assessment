"""Google Gemini provider (free-tier friendly).

All Gemini-specific and network-specific concerns are contained here:
  * reading the API key from config (never hard-coded);
  * asking the model for JSON output;
  * a hard timeout on the call;
  * translating SDK/network errors into our uniform ProviderError types.

Retry/backoff is intentionally NOT here — it lives in the orchestrator so the
policy is uniform across providers (see tutor.py).
"""
from __future__ import annotations

import concurrent.futures

from app.config import settings
from app.llm.base import (
    LLMResult,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            # Fail fast with a clear message rather than a confusing SDK error.
            raise ProviderError(
                "GEMINI_API_KEY is not set. Add it to your .env, or set "
                "LLM_PROVIDER=mock to run offline."
            )
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "google-generativeai is not installed. Run: pip install -r "
                "requirements.txt"
            ) from exc

        genai.configure(api_key=settings.gemini_api_key)
        self._genai = genai
        self.model = settings.gemini_model
        self._model = genai.GenerativeModel(
            model_name=self.model,
            # Ask Gemini to emit JSON directly — reduces (not eliminates) the
            # chance of malformed output. We STILL validate downstream.
            generation_config={"response_mime_type": "application/json"},
        )

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        # Gemini has no separate system role in this SDK path, so we prepend the
        # system prompt. The prompt itself is hardened against injection.
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        # Enforce a hard timeout by running the (blocking) SDK call in a thread
        # and abandoning it if it overruns. This guarantees the API route can
        # never hang indefinitely on a slow provider.
        #
        # NOTE: we manage the executor manually instead of using it as a context
        # manager. A `with` block calls shutdown(wait=True) on exit, which would
        # BLOCK on the hung worker thread and defeat the timeout entirely (caught
        # while testing against a slow live endpoint). shutdown(wait=False) lets
        # us return immediately; the orphaned thread ends when its call resolves.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._call, full_prompt)
        try:
            response = future.result(timeout=settings.request_timeout_s)
        except concurrent.futures.TimeoutError as exc:
            raise ProviderTimeout(
                f"Gemini call exceeded {settings.request_timeout_s}s"
            ) from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        text = getattr(response, "text", "") or ""

        # Token usage, when the SDK reports it.
        prompt_tokens = completion_tokens = None
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_token_count", None)
            completion_tokens = getattr(usage, "candidates_token_count", None)

        return LLMResult(
            text=text,
            model=self.model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _call(self, prompt: str):
        """The actual blocking SDK call, wrapped to normalise errors."""
        try:
            return self._model.generate_content(prompt)
        except Exception as exc:  # noqa: BLE001 - normalise ALL SDK errors
            msg = str(exc).lower()
            # Use precise phrases: a bare "rate" substring also matches unrelated
            # words like "migrate" in Google's error URLs (a real false-positive
            # caught while testing against the live API).
            if "429" in msg or "quota" in msg or "rate limit" in msg \
                    or "resource_exhausted" in msg or "resourceexhausted" in msg:
                raise ProviderRateLimited(str(exc)) from exc
            if "timeout" in msg or "deadline" in msg:
                raise ProviderTimeout(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
