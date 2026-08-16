"""Required scenario 5: the application returns a controlled response when the
model provider fails (timeout, rate limit, malformed output, invalid schema).
Also covers database-failure handling."""
from __future__ import annotations

import app.tutor as tutor
from app.llm.mock import MockProvider


def _force_provider(monkeypatch, mode):
    monkeypatch.setattr(tutor, "get_provider", lambda name=None: MockProvider(mode=mode))


def test_provider_timeout_returns_controlled_response(client, session_id, monkeypatch):
    _force_provider(monkeypatch, "timeout")
    r = client.post(f"/api/sessions/{session_id}/messages",
                    json={"message": "Is 2/5 bigger than 1/2?"})
    assert r.status_code == 200  # NOT a 500
    data = r.json()
    assert data["response"]["confidence"] == 0.0  # fallback marker
    assert "provider_error" in (data["telemetry"]["error"] or "")


def test_rate_limit_returns_controlled_response(client, session_id, monkeypatch):
    _force_provider(monkeypatch, "rate_limited")
    r = client.post(f"/api/sessions/{session_id}/messages",
                    json={"message": "how do I add 1/4 and 1/3?"})
    assert r.status_code == 200
    assert r.json()["response"]["tutorMessage"]  # still a useful message


def test_malformed_json_is_repaired_or_falls_back(client, session_id, monkeypatch):
    _force_provider(monkeypatch, "malformed")
    r = client.post(f"/api/sessions/{session_id}/messages",
                    json={"message": "Is 2/5 bigger than 1/2?"})
    assert r.status_code == 200
    data = r.json()
    assert data["response"]["confidence"] == 0.0
    assert data["telemetry"]["error"] == "malformed_json"


def test_invalid_schema_falls_back(client, session_id, monkeypatch):
    _force_provider(monkeypatch, "invalid_schema")
    r = client.post(f"/api/sessions/{session_id}/messages",
                    json={"message": "Is 2/5 bigger than 1/2?"})
    assert r.status_code == 200
    assert "schema_invalid" in (r.json()["telemetry"]["error"] or "")


def test_unknown_session_returns_404(client):
    r = client.post("/api/sessions/nonexistent/messages", json={"message": "hi"})
    assert r.status_code == 404


def test_empty_message_is_rejected(client, session_id):
    r = client.post(f"/api/sessions/{session_id}/messages", json={"message": ""})
    assert r.status_code == 422  # Pydantic rejects before any processing


def test_database_failure_is_controlled(client, session_id, monkeypatch):
    # Simulate the DB blowing up during message handling.
    import app.db as db

    def boom(*a, **k):
        raise db.DatabaseError("disk gone")

    monkeypatch.setattr(tutor.db, "add_learner_message", boom)
    r = client.post(f"/api/sessions/{session_id}/messages",
                    json={"message": "Is 2/5 bigger than 1/2?"})
    # Controlled 503, not an unhandled crash / stack trace.
    assert r.status_code == 503
