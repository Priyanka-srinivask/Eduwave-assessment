"""SQLite persistence layer (standard-library `sqlite3` only).

Design goals:
  * Readable: every SQL statement is visible here, nothing hidden by an ORM.
  * Safe: all values are passed as bound parameters (never string-formatted),
    which prevents SQL injection.
  * Honest storage: we persist telemetry alongside each turn so observability
    data survives a restart and can be inspected or exported.

Three tables:
  sessions   — one row per tutoring session
  messages   — one row per turn (learner and tutor), with the tutor's structured
               response stored as JSON plus per-turn telemetry
  (telemetry is embedded in `messages` to keep the join-free model simple)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    learner_name  TEXT,
    language      TEXT NOT NULL DEFAULT 'en',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    role            TEXT NOT NULL,            -- 'learner' | 'tutor'
    content         TEXT NOT NULL,            -- learner text, or tutorMessage
    response_json   TEXT,                     -- full TutorResponse (tutor rows)
    client_message_id TEXT,                   -- for idempotency (nullable)
    -- telemetry (nullable; populated on tutor rows) --
    provider        TEXT,
    model           TEXT,
    latency_ms      INTEGER,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    estimated_cost_usd REAL,
    retrieval_ids   TEXT,                     -- JSON array of curriculum IDs
    error           TEXT,                     -- populated when a turn failed
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, created_at);

-- Enforce idempotency: a given client_message_id can appear once per session.
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_idem
    ON messages(session_id, client_message_id)
    WHERE client_message_id IS NOT NULL;
"""


class DatabaseError(Exception):
    """Raised so callers can convert DB failures into controlled API errors
    instead of leaking a raw sqlite3 exception / stack trace to the user."""


def _connect() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may serve requests on different threads.
    # We open a short-lived connection per operation (see `_cursor`), so there is
    # no shared mutable connection to race on.
    conn = sqlite3.connect(
        settings.db_path, timeout=5.0, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")  # better read/write concurrency
    return conn


@contextmanager
def _cursor() -> Iterator[sqlite3.Cursor]:
    """Open a connection, yield a cursor, commit on success, always close.

    Wrapping sqlite3 errors in DatabaseError keeps provider-specific exceptions
    from bubbling up to the API layer.
    """
    conn = _connect()
    try:
        yield conn.cursor()
        conn.commit()
    except sqlite3.Error as exc:  # pragma: no cover - exercised via monkeypatch
        conn.rollback()
        raise DatabaseError(str(exc)) from exc
    finally:
        conn.close()


def init_db() -> None:
    """Create tables/indexes if they do not exist. Safe to call repeatedly."""
    with _cursor() as cur:
        cur.executescript(SCHEMA)


# --- Sessions ---------------------------------------------------------------

def create_session(learner_name: str | None, language: str = "en") -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    now = time.time()
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (id, learner_name, language, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, learner_name, language, now),
        )
    return {
        "id": session_id,
        "learner_name": learner_name,
        "language": language,
        "created_at": now,
    }


def get_session(session_id: str) -> dict[str, Any] | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
    return dict(row) if row else None


# --- Messages ---------------------------------------------------------------

def add_learner_message(
    session_id: str, content: str, client_message_id: str | None = None
) -> str:
    message_id = str(uuid.uuid4())
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO messages (id, session_id, role, content, "
            "client_message_id, created_at) VALUES (?, ?, 'learner', ?, ?, ?)",
            (message_id, session_id, content, client_message_id, time.time()),
        )
    return message_id


def add_tutor_message(
    session_id: str,
    response: dict[str, Any],
    telemetry: dict[str, Any],
) -> str:
    """Persist a tutor turn: the structured response plus its telemetry."""
    message_id = str(uuid.uuid4())
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO messages (id, session_id, role, content, response_json, "
            "provider, model, latency_ms, prompt_tokens, completion_tokens, "
            "estimated_cost_usd, retrieval_ids, error, created_at) "
            "VALUES (?, ?, 'tutor', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                session_id,
                response.get("tutorMessage", ""),
                json.dumps(response),
                telemetry.get("provider"),
                telemetry.get("model"),
                telemetry.get("latency_ms"),
                telemetry.get("prompt_tokens"),
                telemetry.get("completion_tokens"),
                telemetry.get("estimated_cost_usd"),
                json.dumps(telemetry.get("retrieval_ids", [])),
                telemetry.get("error"),
                time.time(),
            ),
        )
    return message_id


def get_messages(session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Return messages for a session, oldest first."""
    query = "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC"
    params: tuple[Any, ...] = (session_id,)
    if limit is not None:
        query += " LIMIT ?"
        params = (session_id, limit)
    with _cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def find_by_client_message_id(
    session_id: str, client_message_id: str
) -> dict[str, Any] | None:
    """Idempotency lookup: has this exact client message already been answered?

    Returns the *tutor* response row that followed the duplicate learner turn,
    if any, so we can replay it instead of calling the model again.
    """
    with _cursor() as cur:
        cur.execute(
            "SELECT * FROM messages WHERE session_id = ? AND client_message_id = ? "
            "AND role = 'learner'",
            (session_id, client_message_id),
        )
        learner = cur.fetchone()
        if learner is None:
            return None
        # The tutor reply is the next tutor row created after this learner turn.
        cur.execute(
            "SELECT * FROM messages WHERE session_id = ? AND role = 'tutor' "
            "AND created_at >= ? ORDER BY created_at ASC LIMIT 1",
            (session_id, learner["created_at"]),
        )
        tutor = cur.fetchone()
    return dict(tutor) if tutor else None
