"""FastAPI application — the HTTP surface.

This layer is intentionally THIN: it validates input, calls the orchestrator,
and maps errors to clean HTTP responses. All real logic lives in the modules it
calls. Routes match the brief's minimum API surface.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import BASE_DIR, settings
from app.models import CreateSessionRequest, PostMessageRequest
from app.tutor import TutorError, handle_message


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the database schema exists before serving any request.
    db.init_db()
    yield


app = FastAPI(
    title="Wavy Tutor",
    version="1.0.0",
    description="A grounded, safe, Socratic AI tutor for Grade-5 fractions.",
    lifespan=lifespan,
)


# --- Health -----------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness + basic config visibility (no secrets)."""
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "model": (
            settings.gemini_model
            if settings.llm_provider == "gemini"
            else "mock-tutor-v1"
        ),
    }


# --- Sessions ---------------------------------------------------------------

@app.post("/api/sessions", status_code=201)
def create_session(body: CreateSessionRequest | None = None) -> dict:
    body = body or CreateSessionRequest()
    try:
        session = db.create_session(body.learner_name, body.language)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail="Storage unavailable") from exc
    return session


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    try:
        session = db.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = db.get_messages(session_id)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail="Storage unavailable") from exc
    return {"session": session, "messages": messages}


@app.post("/api/sessions/{session_id}/messages")
def post_message(session_id: str, body: PostMessageRequest) -> JSONResponse:
    """The main endpoint: submit a learner message, get a validated tutor reply."""
    try:
        result = handle_message(
            session_id, body.message, client_message_id=body.client_message_id
        )
    except TutorError:
        # Orchestrator only raises this for genuinely unrecoverable cases
        # (e.g. unknown session) — everything else degrades to a safe reply.
        raise HTTPException(status_code=404, detail="Session not found") from None
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail="Storage unavailable") from exc
    return JSONResponse(status_code=200, content=result)


# --- Minimal web UI (served last so /api/* takes precedence) ---------------

_WEB_DIR = BASE_DIR / "web"
if _WEB_DIR.exists():
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(_WEB_DIR / "index.html"))

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")
