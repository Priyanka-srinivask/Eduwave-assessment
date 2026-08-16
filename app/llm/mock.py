"""Deterministic, offline mock provider.

Why this exists:
  * the app must be runnable and testable with NO API key (graders, CI);
  * tests need *deterministic* output to assert on;
  * reliability tests need a way to simulate provider failures.

The mock reads the fractions and curriculum IDs already present in the prompt
and produces a valid, Socratic-looking TutorResponse JSON. Because it echoes the
IDs the orchestrator injected, its citations are naturally grounded — which lets
the citation-grounding tests exercise real behaviour without a live model.

`mode` lets tests force specific failure shapes.
"""
from __future__ import annotations

import json
import re

from app.llm.base import (
    LLMResult,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)

_FRACTION_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
_ID_RE = re.compile(r"fractions\.[a-z]+\.\d+")


class MockProvider:
    name = "mock"

    def __init__(self, mode: str = "normal", model: str = "mock-tutor-v1"):
        # modes: normal | malformed | invalid_schema | timeout | rate_limited | empty
        self.mode = mode
        self.model = model

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        # --- Failure simulations for reliability tests ---
        if self.mode == "timeout":
            raise ProviderTimeout("mock: simulated timeout")
        if self.mode == "rate_limited":
            raise ProviderRateLimited("mock: simulated 429")
        if self.mode == "error":
            raise ProviderError("mock: simulated provider error")
        if self.mode == "malformed":
            # Not valid JSON at all — exercises the JSON-repair path.
            text = "Sure! Here is your answer: the tutorMessage is ```{not json"
            return self._result(text)
        if self.mode == "invalid_schema":
            # Valid JSON, but violates the schema (confidence out of range,
            # missing tutorMessage) — exercises Pydantic rejection.
            text = json.dumps({"confidence": 5, "misconception": "x"})
            return self._result(text)
        if self.mode == "empty":
            return self._result("")

        # --- Normal deterministic response ---
        fractions = _FRACTION_RE.findall(system_prompt + " " + user_prompt)
        ids = _ID_RE.findall(user_prompt)  # IDs the orchestrator injected

        if fractions:
            frac_a = fractions[0]
            tutor_msg = (
                f"Great question! Let's reason about {frac_a} together instead of "
                "jumping to the answer. What do you notice about the size of the "
                "pieces in each fraction?"
            )
            next_q = (
                f"Could you compare {frac_a} to one-half first — is it more or "
                "less than 1/2?"
            )
        else:
            tutor_msg = (
                "Let's take this one step at a time. Tell me what you already "
                "know about this, and we'll build from there."
            )
            next_q = "What part of the problem feels trickiest right now?"

        # Pick a misconception label from the injected context, if any.
        misconception = None
        if "common denominator" in user_prompt.lower():
            misconception = "numerator_only_comparison"
        elif "equivalent" in user_prompt.lower():
            misconception = "different_looking_fractions_cannot_be_equal"

        payload = {
            "tutorMessage": tutor_msg,
            "misconception": misconception,
            "nextQuestion": next_q,
            # Deterministic heuristic confidence: more retrieved context and a
            # concrete fraction => higher confidence.
            "confidence": round(min(0.5 + 0.1 * len(ids) + (0.1 if fractions else 0), 0.95), 2),
            # Cite up to two retrieved IDs. If the model "wanted" to invent one,
            # the orchestrator would strip it — here we stay grounded on purpose.
            "curriculumCitations": ids[:2],
            "safetyFlags": [],
        }
        return self._result(json.dumps(payload))

    def _result(self, text: str) -> LLMResult:
        # Rough deterministic token estimate: ~4 chars/token.
        return LLMResult(
            text=text,
            model=self.model,
            provider=self.name,
            prompt_tokens=None,
            completion_tokens=max(1, len(text) // 4),
        )
