"""Shared pytest fixtures.

Key design choice: every test runs against a FRESH, ISOLATED SQLite database in
a temp directory, and forces the offline mock provider. So the whole suite runs
deterministically with no API key and no shared state between tests.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient with an isolated DB and the mock provider.

    We point the DB at a temp file and reload the modules that captured settings
    at import time, so each test is fully isolated.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    # Keep retry/backoff instant during tests so failure-path tests stay fast.
    monkeypatch.setenv("RETRY_BASE_DELAY_S", "0")
    monkeypatch.setenv("MAX_RETRIES", "1")

    # Reload config + modules so they pick up the patched environment.
    import app.config as config
    importlib.reload(config)
    import app.db as db
    importlib.reload(db)
    import app.observability as observability
    importlib.reload(observability)
    import app.llm as llm
    importlib.reload(llm)
    import app.tutor as tutor
    importlib.reload(tutor)
    import app.main as main
    importlib.reload(main)

    # Using the context manager runs lifespan -> init_db().
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def session_id(client):
    """Create a session and return its id."""
    resp = client.post("/api/sessions", json={"learner_name": "Test", "language": "en"})
    assert resp.status_code == 201
    return resp.json()["id"]
