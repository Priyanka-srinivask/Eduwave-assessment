"""Input safety and prompt-injection resistance.

Design principle: DEFENSE IN DEPTH.

  Layer 1 (this file): a deterministic pre-filter that runs BEFORE the model.
      Pattern-based checks cannot be "talked out of" the way an LLM can, so the
      strongest guarantees live here. If we detect a hard-block category we can
      skip the model entirely and return a safe, canned, age-appropriate reply.

  Layer 2 (prompt.py): the system prompt also instructs the model to refuse
      injection / off-topic / inappropriate content, as a backstop for phrasings
      the pre-filter misses.

  Layer 3 (models.py + tutor.py): structured validation + citation grounding
      ensure that even a "successful" jailbreak cannot make the app emit an
      invalid shape or fabricated curriculum references.

This layered approach is intentional: no single regex is complete, but a
regex + LLM instruction + output validation together are hard to defeat.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import SafetyFlag


@dataclass
class SafetyVerdict:
    """Result of screening a learner message."""
    flags: list[SafetyFlag]
    # If True, we must NOT call the model; return `safe_response` instead.
    block: bool
    safe_response: str | None = None

    @property
    def is_flagged(self) -> bool:
        return bool(self.flags)


# --- Pattern libraries ------------------------------------------------------
# Kept explicit and readable so a reviewer can audit exactly what we match.

# Attempts to override the system instruction / extract the hidden prompt.
_INJECTION_PATTERNS = [
    r"ignore (all|any|the)? ?(previous|prior|above)? ?instructions?",
    r"disregard (the|your|all)? ?(previous|prior|above)? ?(instructions?|rules?)",
    r"forget (everything|all|your) (instructions?|rules?|training)",
    r"you are now\b",
    r"(you are|act as|pretend to be|pretend you are)\b.*(?:dan|jailbreak|unfiltered|developer mode|no rules|without restrictions)",
    r"pretend (to be|you are)\b",
    r"new instructions?:",
    r"override (your|the) (rules?|instructions?|safety)",
]

# Attempts to reveal the system prompt / internal configuration.
_PROMPT_PROBE_PATTERNS = [
    r"(show|reveal|print|repeat|tell me|what is|what's) (your|the) (system )?(prompt|instructions?|rules?)",
    r"what were you (told|instructed|programmed)",
    r"repeat (everything|the text) above",
    r"reveal your (hidden )?(prompt|guidelines?|configuration)",
]

# A compact profanity list. In production this would be a maintained lexicon or
# a moderation API; kept short here and easily extended.
_PROFANITY = {
    "damn", "hell", "crap", "shit", "fuck", "fucking", "bitch", "asshole",
    "bastard", "dick", "piss",
}

# Signals of content inappropriate for a Grade-5 learning context.
_AGE_INAPPROPRIATE_PATTERNS = [
    r"\b(sex|sexual|porn|nude|naked)\b",
    r"\b(kill|suicide|self[- ]?harm|drugs?|cocaine|heroin)\b",
    # Weapon-making intent in either word order ("make a bomb" / "bomb to build").
    r"\b(make|build|buy|create)\b.*\b(gun|weapon|bomb|explosive)\b",
    r"\b(gun|weapon|bomb|explosive)\b.*\b(make|build|buy|create)\b",
]

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]
_PROBE_RE = [re.compile(p, re.IGNORECASE) for p in _PROMPT_PROBE_PATTERNS]
_AGE_RE = [re.compile(p, re.IGNORECASE) for p in _AGE_INAPPROPRIATE_PATTERNS]
_WORD_RE = re.compile(r"[a-zA-Z']+")


# --- Canned safe responses --------------------------------------------------
# Age-appropriate, redirecting, never scolding. These are returned WITHOUT
# calling the model when we hard-block.

_SAFE_REDIRECT = (
    "I'm here to help you learn about fractions! Let's keep going with math. "
    "What would you like to work on — comparing, adding, or understanding "
    "what a fraction means?"
)
_SAFE_INJECTION = (
    "I'm your fractions tutor, so I'll stick to helping you learn math. "
    "What fractions question can I help you think through?"
)
_SAFE_INAPPROPRIATE = (
    "That's not something I can help with. I'm your fractions tutor — "
    "let's focus on math. Want to try a fractions problem together?"
)


def screen_message(text: str) -> SafetyVerdict:
    """Screen a learner message before it reaches the model.

    Returns a verdict listing any safety flags and, for hard-block categories,
    a safe canned response so the caller can bypass the LLM entirely.
    """
    flags: list[SafetyFlag] = []

    # 1. Prompt-injection override attempts (hard block).
    if any(rx.search(text) for rx in _INJECTION_RE):
        flags.append(SafetyFlag.PROMPT_INJECTION)

    # 2. System-prompt probing (hard block).
    if any(rx.search(text) for rx in _PROBE_RE):
        flags.append(SafetyFlag.SYSTEM_PROMPT_PROBE)

    # 3. Age-inappropriate content (hard block).
    if any(rx.search(text) for rx in _AGE_RE):
        flags.append(SafetyFlag.AGE_INAPPROPRIATE)

    # 4. Profanity (flag, but we still try to help — kids test boundaries).
    words = {w.lower() for w in _WORD_RE.findall(text)}
    if words & _PROFANITY:
        flags.append(SafetyFlag.PROFANITY)

    # Decide whether to block (skip the model) and which safe reply to use.
    if SafetyFlag.AGE_INAPPROPRIATE in flags:
        return SafetyVerdict(flags, block=True, safe_response=_SAFE_INAPPROPRIATE)
    if SafetyFlag.PROMPT_INJECTION in flags or SafetyFlag.SYSTEM_PROMPT_PROBE in flags:
        return SafetyVerdict(flags, block=True, safe_response=_SAFE_INJECTION)
    if SafetyFlag.PROFANITY in flags:
        # Do not hard-block: redirect politely while still allowing learning.
        return SafetyVerdict(flags, block=True, safe_response=_SAFE_REDIRECT)

    return SafetyVerdict(flags, block=False)
