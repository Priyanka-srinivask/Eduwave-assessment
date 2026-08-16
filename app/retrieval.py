"""Curriculum retrieval — the grounding layer.

We deliberately use a transparent, explainable keyword/hybrid scorer rather than
embeddings. The brief says embeddings are welcome but not required, and for a
small fixed corpus (8 items) a well-reasoned lexical approach is both sufficient
and *fully inspectable* — we can show exactly why an item was selected, which is
worth more here than a black-box similarity score.

Scoring for a query against a curriculum item is the sum of:
  * keyword hits          — query term matches an item's curated `keywords`  (x3)
  * title hits            — query term appears in the title                   (x2)
  * content hits          — query term appears in the content body           (x1)
  * misconception hits    — query term appears in a listed misconception     (x2)
  * fraction/number signal— shared numeric fraction tokens (e.g. "1/2")      (x2)

Weights favour curated keywords and titles because those are the strongest
signals of topical relevance. Every match is recorded so the API can explain
*why* an item was chosen.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import DATA_DIR

# Words too common to be useful as retrieval signal. Kept small on purpose;
# over-aggressive stopword lists hurt more than they help on a tiny corpus.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "am", "of", "to", "and", "or", "in", "on",
    "do", "does", "how", "what", "why", "i", "you", "it", "this", "that", "with",
    "for", "can", "my", "me", "we", "be", "so", "if", "than", "then", "not",
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:/[0-9]+)?")  # keeps fraction tokens like 3/4
_FRACTION_RE = re.compile(r"\b\d+\s*/\s*\d+\b")


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = _WORD_RE.findall(text)
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


@dataclass
class RetrievedItem:
    """One scored curriculum item plus a human-readable explanation."""
    id: str
    title: str
    content: str
    score: float
    matched_terms: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class CurriculumItem:
    id: str
    title: str
    content: str
    keywords: list[str]
    common_misconceptions: list[str]


@lru_cache(maxsize=1)
def load_curriculum(path: str | None = None) -> tuple[CurriculumItem, ...]:
    """Load and cache the curriculum. Cached because the file never changes at
    runtime; the tuple return type makes the cache value immutable."""
    p = Path(path) if path else DATA_DIR / "curriculum.json"
    if not p.exists():
        raise FileNotFoundError(f"Curriculum file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = tuple(
        CurriculumItem(
            id=obj["id"],
            title=obj["title"],
            content=obj["content"],
            keywords=obj.get("keywords", []),
            common_misconceptions=obj.get("common_misconceptions", []),
        )
        for obj in raw
    )
    if not items:
        raise ValueError("Curriculum is empty.")
    return items


def _score(query_tokens: set[str], query_fractions: set[str],
           item: CurriculumItem) -> RetrievedItem:
    score = 0.0
    matched: set[str] = set()
    reasons: list[str] = []

    keyword_tokens = {t for kw in item.keywords for t in _tokenize(kw)}
    title_tokens = set(_tokenize(item.title))
    content_tokens = set(_tokenize(item.content))
    misconception_tokens = {
        t for m in item.common_misconceptions for t in _tokenize(m)
    }

    for term in query_tokens:
        if term in keyword_tokens:
            score += 3.0
            matched.add(term)
        if term in title_tokens:
            score += 2.0
            matched.add(term)
        if term in content_tokens:
            score += 1.0
            matched.add(term)
        if term in misconception_tokens:
            score += 2.0
            matched.add(term)

    if keyword_tokens & query_tokens:
        reasons.append(
            "keyword match: " + ", ".join(sorted(keyword_tokens & query_tokens))
        )
    if title_tokens & query_tokens:
        reasons.append(
            "title match: " + ", ".join(sorted(title_tokens & query_tokens))
        )

    # Numeric fraction signal: if the learner mentions a fraction and the item's
    # content discusses fractions/comparison, nudge the score.
    item_fractions = set(_FRACTION_RE.findall(item.content))
    if query_fractions and item_fractions:
        score += 2.0
        reasons.append("both mention explicit fractions")

    return RetrievedItem(
        id=item.id,
        title=item.title,
        content=item.content,
        score=score,
        matched_terms=sorted(matched),
        reasons=reasons,
    )


def retrieve(query: str, top_k: int = 3,
             curriculum_path: str | None = None) -> list[RetrievedItem]:
    """Return the top-k most relevant curriculum items for a query.

    Items with a zero score are excluded, so a totally off-topic message yields
    no context (the tutor is then told it has no grounding — see prompt.py).
    """
    items = load_curriculum(curriculum_path)
    query_tokens = set(_tokenize(query))
    query_fractions = set(_FRACTION_RE.findall(query))

    scored = [_score(query_tokens, query_fractions, it) for it in items]
    scored = [s for s in scored if s.score > 0]
    scored.sort(key=lambda r: (r.score, r.id), reverse=True)
    return scored[:top_k]
