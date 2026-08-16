"""Required scenario 2: structured output validation rejects or repairs malformed
model output. Also covers the citation-grounding guarantee."""
from __future__ import annotations

import pytest

from app.models import TutorResponse
from app.tutor import _extract_json, _ground_citations


def test_valid_output_parses():
    raw = {
        "tutorMessage": "Let's compare the pieces.",
        "misconception": "numerator_only_comparison",
        "nextQuestion": "What denominator could 3 and 4 share?",
        "confidence": 0.84,
        "curriculumCitations": ["fractions.compare.02"],
        "safetyFlags": [],
    }
    resp = TutorResponse.parse_model_json(raw)
    assert resp.confidence == 0.84
    assert resp.curriculumCitations == ["fractions.compare.02"]


def test_confidence_out_of_range_is_rejected():
    with pytest.raises(Exception):
        TutorResponse.parse_model_json({"tutorMessage": "hi", "confidence": 5})


def test_missing_required_field_is_rejected():
    # No tutorMessage -> invalid.
    with pytest.raises(Exception):
        TutorResponse.parse_model_json({"confidence": 0.5})


def test_unknown_safety_flag_is_rejected():
    with pytest.raises(Exception):
        TutorResponse.parse_model_json(
            {"tutorMessage": "hi", "confidence": 0.5, "safetyFlags": ["totally_made_up"]}
        )


def test_repair_extracts_json_from_markdown_fence():
    messy = '```json\n{"tutorMessage":"hi","confidence":0.7}\n```'
    parsed = _extract_json(messy)
    assert parsed == {"tutorMessage": "hi", "confidence": 0.7}


def test_repair_extracts_json_from_chatty_text():
    messy = 'Sure! Here you go: {"tutorMessage":"hi","confidence":0.5} hope that helps!'
    parsed = _extract_json(messy)
    assert parsed["tutorMessage"] == "hi"


def test_repair_returns_none_for_unparseable():
    assert _extract_json("this is not json at all {oops") is None
    assert _extract_json("") is None


def test_grounding_strips_invented_citations():
    resp = TutorResponse(
        tutorMessage="x", confidence=0.5,
        curriculumCitations=["fractions.compare.02", "fractions.FAKE.99"],
    )
    grounded = _ground_citations(resp, allowed_ids={"fractions.compare.02"})
    assert grounded.curriculumCitations == ["fractions.compare.02"]


def test_grounding_keeps_only_retrieved_ids():
    resp = TutorResponse(
        tutorMessage="x", confidence=0.5,
        curriculumCitations=["fractions.add.01", "fractions.add.02"],
    )
    grounded = _ground_citations(resp, allowed_ids=set())  # nothing retrieved
    assert grounded.curriculumCitations == []
