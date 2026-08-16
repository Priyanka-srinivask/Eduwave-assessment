"""Pydantic schemas — the typed contracts for the whole application.

The most important class here is `TutorResponse`: it is the structured output
contract from the brief. We validate every model reply against it, so the rest
of the system (and the frontend) can trust the shape even though the LLM cannot
be trusted to produce it correctly on its own.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# --- API request bodies -----------------------------------------------------

class CreateSessionRequest(BaseModel):
    """Optional metadata when a learner starts a session."""
    learner_name: str | None = Field(
        default=None, max_length=80,
        description="Optional display name; never required, never logged in full.",
    )
    language: str = Field(default="en", description="'en' or 'es' (bonus).")


class PostMessageRequest(BaseModel):
    """A single learner turn."""
    message: str = Field(min_length=1, max_length=2000)
    # Optional client-supplied id to make duplicate submissions idempotent (bonus).
    client_message_id: str | None = Field(default=None, max_length=100)


# --- The structured tutor response contract ---------------------------------

class SafetyFlag(str, Enum):
    """Closed vocabulary of safety reasons. Using an enum (not free text) means
    the model cannot invent arbitrary flags and downstream code can branch on
    known values."""
    PROFANITY = "profanity"
    PROMPT_INJECTION = "prompt_injection"
    OFF_TOPIC = "off_topic"
    AGE_INAPPROPRIATE = "age_inappropriate"
    SYSTEM_PROMPT_PROBE = "system_prompt_probe"


class TutorResponse(BaseModel):
    """Validated structure the backend guarantees to return.

    Matches the brief's required contract. Extra fields are allowed by the brief
    ("you may add fields, but do not remove the required ones") — we add none to
    the model itself and keep telemetry separate, so the contract stays clean.
    """
    # Required by the brief:
    tutorMessage: str = Field(min_length=1)
    misconception: str | None = None
    nextQuestion: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    curriculumCitations: list[str] = Field(default_factory=list)
    safetyFlags: list[SafetyFlag] = Field(default_factory=list)

    @field_validator("curriculumCitations")
    @classmethod
    def _dedupe_citations(cls, v: list[str]) -> list[str]:
        # Deterministic, de-duplicated ordering. The *authoritative* filtering of
        # invented citations happens in the tutor orchestrator against the IDs we
        # actually retrieved — this validator only tidies the list shape.
        seen: set[str] = set()
        out: list[str] = []
        for c in v:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    @classmethod
    def parse_model_json(cls, raw: dict[str, Any]) -> "TutorResponse":
        """Validate a dict decoded from the model's JSON output.

        Kept separate from `model_validate` so callers have one obvious entry
        point and so we can evolve repair logic in one place if needed.
        """
        return cls.model_validate(raw)
