"""Required scenario 3: a follow-up message uses prior session context."""
from __future__ import annotations


def test_followup_uses_prior_context(client, session_id):
    # First turn establishes the topic (comparing 2/5 and 1/2).
    r1 = client.post(f"/api/sessions/{session_id}/messages",
                     json={"message": "Is 2/5 bigger than 1/2?"})
    assert r1.status_code == 200

    # Follow-up has NO fraction of its own. If memory works, the tutor's reply
    # still references the earlier fraction (the mock echoes it from history).
    r2 = client.post(f"/api/sessions/{session_id}/messages",
                     json={"message": "I still do not understand"})
    assert r2.status_code == 200
    reply = r2.json()["response"]
    combined = (reply["tutorMessage"] + " " + (reply["nextQuestion"] or ""))
    assert "2/5" in combined or "1/2" in combined, (
        "follow-up reply should reference the fraction from prior context"
    )


def test_session_accumulates_messages(client, session_id):
    for msg in ["what is a numerator?", "and a denominator?", "thanks"]:
        client.post(f"/api/sessions/{session_id}/messages", json={"message": msg})
    got = client.get(f"/api/sessions/{session_id}").json()
    # 3 learner + 3 tutor turns persisted.
    assert len(got["messages"]) == 6
    roles = [m["role"] for m in got["messages"]]
    assert roles.count("learner") == 3
    assert roles.count("tutor") == 3


def test_history_is_ordered_oldest_first(client, session_id):
    client.post(f"/api/sessions/{session_id}/messages", json={"message": "first"})
    client.post(f"/api/sessions/{session_id}/messages", json={"message": "second"})
    msgs = client.get(f"/api/sessions/{session_id}").json()["messages"]
    learner_msgs = [m["content"] for m in msgs if m["role"] == "learner"]
    assert learner_msgs == ["first", "second"]
