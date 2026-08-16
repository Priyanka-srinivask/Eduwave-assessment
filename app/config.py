"""Central configuration for Wavy Tutor.

Everything tunable lives here and is loaded from environment variables (or a
local .env file in development). Keeping config in one place means:
  * no secret (API key) is ever hard-coded in source, and
  * behaviour (model, timeouts, retrieval size) can change without code edits.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute paths derived from this file's location, so the app runs the same
# regardless of the working directory it is launched from.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    # --- LLM provider ---
    # "gemini" uses the real API (needs GEMINI_API_KEY); "mock" runs fully
    # offline with a deterministic fake model. Tests always force "mock".
    llm_provider: str = "mock"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"  # free-tier, fast, good JSON support

    # --- Reliability knobs ---
    request_timeout_s: float = 20.0  # hard cap on a single model call
    max_retries: int = 2             # retries on transient errors (bonus: backoff)
    retry_base_delay_s: float = 0.5  # first backoff delay; doubles each retry

    # --- Retrieval ---
    retrieval_top_k: int = 3         # how many curriculum items to give the model

    # --- Memory ---
    history_window: int = 8          # recent messages included in the prompt

    # --- Persistence ---
    db_path: str = str(BASE_DIR / "wavy_tutor.db")

    # --- Cost model (documented, not billed) ---
    # Gemini 1.5 Flash free-tier is $0, but we still compute an *estimated* cost
    # using published paid rates so observability is meaningful. USD per 1M tokens.
    cost_per_1m_input_tokens: float = 0.075
    cost_per_1m_output_tokens: float = 0.30

    # Load from a .env file if present; ignore unknown env vars.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


# A single shared settings instance imported across the app.
settings = Settings()
