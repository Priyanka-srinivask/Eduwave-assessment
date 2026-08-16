"""Observability helpers: cost estimation and safe structured logging.

The brief requires recording latency, model/provider, token usage, estimated
cost, retrieval IDs and failures — WITHOUT logging secrets or unnecessary
student content. So we log telemetry fields explicitly and never dump the raw
learner message or the API key.
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("wavy_tutor")


def estimate_cost_usd(prompt_tokens: int | None,
                      completion_tokens: int | None) -> float:
    """Estimated cost using documented per-token rates from config.

    Gemini's free tier bills $0 in practice; this figure exists so observability
    is meaningful and so the same code would report real cost on a paid tier.
    """
    pt = prompt_tokens or 0
    ct = completion_tokens or 0
    cost = (
        pt / 1_000_000 * settings.cost_per_1m_input_tokens
        + ct / 1_000_000 * settings.cost_per_1m_output_tokens
    )
    return round(cost, 8)


def log_turn(telemetry: dict) -> None:
    """Log one turn's telemetry. Deliberately logs only non-sensitive fields:
    NO learner message text, NO API key, NO full model output."""
    logger.info(
        "turn provider=%s model=%s latency_ms=%s prompt_tokens=%s "
        "completion_tokens=%s cost_usd=%s retrieval_ids=%s error=%s",
        telemetry.get("provider"),
        telemetry.get("model"),
        telemetry.get("latency_ms"),
        telemetry.get("prompt_tokens"),
        telemetry.get("completion_tokens"),
        telemetry.get("estimated_cost_usd"),
        telemetry.get("retrieval_ids"),
        telemetry.get("error"),
    )
