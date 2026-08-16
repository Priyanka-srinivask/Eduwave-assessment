"""The orchestrator: turns a learner message into a validated tutor response.

This is the request lifecycle in code. It ties together safety, memory,
retrieval, prompting, the LLM call (with retry/backoff), structured-output
validation, citation grounding, persistence and observability.

Every failure mode returns a controlled `TutorResponse` — the caller (API layer)
never has to handle a half-built or exceptional result from here.
"""
from __future__ import annotations

import json
import re
import time

from app import db
from app.config import settings
from app.llm import get_provider
from app.llm.base import LLMResult, ProviderError
from app.models import SafetyFlag, TutorResponse
from app.observability import estimate_cost_usd, log_turn
from app.prompt import SYSTEM_PROMPT, build_user_prompt
from app.retrieval import retrieve
from app.safety import screen_message

# Matches the first {...} JSON object in a string (used to repair chatty output).
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class TutorError(Exception):
    """Raised for unrecoverable errors the API layer should turn into a 4xx/5xx
    (e.g. the session does not exist). Model/provider failures do NOT raise —
    they degrade to a controlled TutorResponse instead."""


# --- JSON parsing / repair --------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Best-effort parse of model output into a dict.

    Strategy: try strict JSON first; if that fails, strip common markdown fences
    and try to grab the first {...} block. Returns None if nothing parses.
    """
    if not text or not text.strip():
        return None
    # 1. Strict.
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # 2. Strip code fences and retry.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        try:
            obj = json.loads(cleaned)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    # 3. Grab the first {...} block.
    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _ground_citations(response: TutorResponse, allowed_ids: set[str]) -> TutorResponse:
    """THE anti-hallucination guarantee.

    Drop any citation the model produced that was not among the IDs we actually
    retrieved and supplied. This is done in code — not left to the prompt —
    because the model cannot be trusted to obey the grounding instruction.
    """
    grounded = [c for c in response.curriculumCitations if c in allowed_ids]
    if grounded != response.curriculumCitations:
        response = response.model_copy(update={"curriculumCitations": grounded})
    return response


# --- Controlled fallbacks ---------------------------------------------------

def _fallback_response(reason: str, citations: list[str]) -> TutorResponse:
    """A safe, useful reply when the model fails or returns unusable output."""
    return TutorResponse(
        tutorMessage=(
            "I'm having a little trouble thinking that through right now. "
            "Let's try again — can you tell me what you've worked out so far, "
            "or restate your fractions question?"
        ),
        misconception=None,
        nextQuestion="What part of the problem should we look at first?",
        confidence=0.0,  # 0 signals this was NOT a model-derived answer
        curriculumCitations=citations,
        safetyFlags=[],
    )


def _call_with_retry(system_prompt: str, user_prompt: str) -> LLMResult:
    """Call the provider with bounded exponential backoff (retry policy lives
    here so it is uniform across providers). Re-raises ProviderError if all
    attempts fail, so the caller can fall back."""
    provider = get_provider()
    attempts = settings.max_retries + 1
    last_exc: ProviderError | None = None
    for attempt in range(attempts):
        try:
            return provider.generate(system_prompt, user_prompt)
        except ProviderError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = settings.retry_base_delay_s * (2 ** attempt)
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# --- Main entry point -------------------------------------------------------

def handle_message(
    session_id: str,
    learner_message: str,
    client_message_id: str | None = None,
) -> dict:
    """Process one learner turn end-to-end. Returns a dict with the validated
    response plus telemetry. Never raises for model/provider problems."""
    session = db.get_session(session_id)
    if session is None:
        raise TutorError(f"Session not found: {session_id}")

    # Idempotency (bonus): if this exact client message was already answered,
    # replay the stored response instead of calling the model again.
    if client_message_id:
        prior = db.find_by_client_message_id(session_id, client_message_id)
        if prior and prior.get("response_json"):
            return {
                "response": json.loads(prior["response_json"]),
                "telemetry": {"idempotent_replay": True},
                "session_id": session_id,
            }

    started = time.perf_counter()
    language = session.get("language", "en")

    # Persist the learner turn first, so it is part of history and not lost even
    # if the model call fails.
    db.add_learner_message(session_id, learner_message, client_message_id)

    # 1. SAFETY (pre-model). Hard-block categories skip the model entirely.
    verdict = screen_message(learner_message)
    if verdict.block:
        response = TutorResponse(
            tutorMessage=verdict.safe_response or "Let's keep our focus on fractions.",
            misconception=None,
            nextQuestion="What fractions problem can we work on together?",
            confidence=1.0,  # we are certain about the safe action
            curriculumCitations=[],
            safetyFlags=verdict.flags,
        )
        telemetry = _telemetry(
            provider="none", model="safety_prefilter",
            latency_ms=_ms(started), result=None, retrieval_ids=[],
            error=None,
        )
        db.add_tutor_message(session_id, response.model_dump(mode="json"), telemetry)
        log_turn(telemetry)
        return {"response": response.model_dump(mode="json"),
                "telemetry": telemetry, "session_id": session_id}

    # 2. MEMORY: recent conversation for coherent follow-ups.
    history = db.get_messages(session_id)

    # 3. RETRIEVE grounding context.
    retrieved = retrieve(learner_message, top_k=settings.retrieval_top_k)
    allowed_ids = {r.id for r in retrieved}
    retrieval_ids = [r.id for r in retrieved]

    # 4. BUILD prompt.
    user_prompt = build_user_prompt(
        learner_message, retrieved, history,
        max_history_turns=settings.history_window, language=language,
    )

    # 5. CALL model with retry/backoff; degrade gracefully on failure.
    result: LLMResult | None = None
    error: str | None = None
    try:
        result = _call_with_retry(SYSTEM_PROMPT, user_prompt)
    except ProviderError as exc:
        error = f"provider_error: {type(exc).__name__}"
        response = _fallback_response(str(exc), retrieval_ids)

    # 6. VALIDATE + GROUND (only if the model returned something).
    if result is not None:
        parsed = _extract_json(result.text)
        if parsed is None:
            error = "malformed_json"
            response = _fallback_response("malformed json", retrieval_ids)
        else:
            try:
                response = TutorResponse.parse_model_json(parsed)
                response = _ground_citations(response, allowed_ids)
            except Exception as exc:  # pydantic ValidationError etc.
                error = f"schema_invalid: {type(exc).__name__}"
                response = _fallback_response("schema invalid", retrieval_ids)

    # 7. PERSIST + OBSERVE.
    telemetry = _telemetry(
        provider=result.provider if result else "unknown",
        model=result.model if result else "unknown",
        latency_ms=_ms(started),
        result=result,
        retrieval_ids=retrieval_ids,
        error=error,
    )
    try:
        db.add_tutor_message(session_id, response.model_dump(mode="json"), telemetry)
    except db.DatabaseError:
        # Persisting telemetry must not break the user's reply.
        telemetry["error"] = (telemetry.get("error") or "") + ";persist_failed"
    log_turn(telemetry)

    return {"response": response.model_dump(mode="json"),
            "telemetry": telemetry, "session_id": session_id}


# --- small helpers ----------------------------------------------------------

def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _telemetry(provider: str, model: str, latency_ms: int,
               result: LLMResult | None, retrieval_ids: list[str],
               error: str | None) -> dict:
    pt = result.prompt_tokens if result else None
    ct = result.completion_tokens if result else None
    return {
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "estimated_cost_usd": estimate_cost_usd(pt, ct),
        "retrieval_ids": retrieval_ids,
        "error": error,
    }
