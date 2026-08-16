"""Required scenario 1: retrieval returns relevant curriculum IDs for a known
fractions question."""
from __future__ import annotations

from app.retrieval import load_curriculum, retrieve


def test_retrieve_comparison_question_returns_compare_items():
    results = retrieve("Is 2/5 bigger than 1/2?", top_k=3)
    assert results, "expected at least one relevant item"
    ids = {r.id for r in results}
    # A comparison question should surface comparison curriculum.
    assert any(i.startswith("fractions.compare") for i in ids), ids


def test_retrieve_addition_question_returns_add_items():
    results = retrieve("how do I add 1/4 and 1/3?", top_k=3)
    ids = {r.id for r in results}
    assert "fractions.add.02" in ids or "fractions.add.01" in ids, ids


def test_retrieve_numerator_question_returns_foundations():
    results = retrieve("what is a numerator?", top_k=3)
    assert results[0].id == "fractions.foundations.01"


def test_offtopic_question_returns_nothing():
    # No shared vocabulary with the curriculum -> no grounding.
    assert retrieve("tell me a joke about cats", top_k=3) == []


def test_results_include_explanation():
    results = retrieve("compare fractions with a common denominator", top_k=3)
    assert results[0].reasons, "each result should explain why it matched"
    assert results[0].matched_terms


def test_curriculum_ids_are_preserved():
    # The brief requires preserving IDs so citations can be verified.
    ids = {i.id for i in load_curriculum()}
    assert "fractions.compare.02" in ids
    assert len(ids) == 8
