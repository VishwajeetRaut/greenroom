"""
Guardrail layer — stops the interviewer from leaking the answer inside its own
question. Two concrete leaks we must never let through:

  technical       — stating the time/space complexity of the candidate's (or the
                     optimal) solution instead of asking the candidate to derive it.
  system-design   — declaring a specific architectural decision (which database,
                     caching layer, scaling pattern, etc.) instead of asking the
                     candidate to propose and defend one.

Defense in depth, same pattern used by production moderation pipelines:
  1. Prompt hardening (see TRACK_PERSONAS in llm.py) — primary defense, cheapest,
     catches the vast majority of cases.
  2. Output-side detector below — regex patterns catch the residual cases where
     the model leaks anyway. On a hit, the caller gets one chance to regenerate
     with a corrective instruction; if that still leaks, we fall back to a
     pre-written, known-safe question so the candidate never sees a leaked answer.

This is intentionally pattern-based rather than a second LLM call ("LLM judge")
so it adds near-zero latency and never depends on a third LLM request succeeding.
"""

from __future__ import annotations

import random
import re

_COMPLEXITY_PATTERNS = [
    re.compile(r"O\(\s*[a-zA-Z0-9log\s\*\+\^,]+\s*\)"),
    re.compile(r"\b(time|space)\s+complexity\s+(is|would be|of (your|this|the)\s+\w+\s+is)\b", re.IGNORECASE),
    re.compile(r"\bruns?\s+in\s+(linear|constant|logarithmic|log[- ]?linear|quadratic|exponential|polynomial)\s+time\b", re.IGNORECASE),
    re.compile(r"\b(optimal|best|ideal)\s+(time|space)\s+complexity\b", re.IGNORECASE),
    re.compile(r"\byour solution (is|runs)\s+O\(", re.IGNORECASE),
]

_ARCHITECTURE_LEAK_PATTERNS = [
    re.compile(r"\byou should (use|implement|add|build|adopt)\b", re.IGNORECASE),
    re.compile(r"\bi('d| would) (recommend|suggest)\b", re.IGNORECASE),
    re.compile(r"\bthe (best|right|correct|optimal) (approach|architecture|design|way|solution) (is|would be) to\b", re.IGNORECASE),
    re.compile(r"\byou('ll| will) (need|want) to (use|implement|add)\b", re.IGNORECASE),
    re.compile(r"\bthe (key|main) (architectural\s+)?decision (is|here is|would be) to\b", re.IGNORECASE),
]

_LEAK_PATTERNS = {
    "technical": _COMPLEXITY_PATTERNS,
    "system-design": _ARCHITECTURE_LEAK_PATTERNS,
}

_FALLBACK_QUESTIONS = {
    "technical": [
        "Before we move on — how would you characterize the efficiency of your solution, and could it be improved?",
        "What trade-offs did you weigh when you picked this approach over the alternatives?",
        "Are there any edge cases your current solution might not handle correctly?",
        "Walk me through what happens to your solution as the input grows much larger.",
    ],
    "system-design": [
        "What are the main trade-offs of the approach you're describing?",
        "How would this design hold up if traffic increased by 10x overnight?",
        "What would you reconsider first if one of these components failed in production?",
        "Where do you expect this design to break down first, and why?",
    ],
}


def violates(text: str, track: str) -> bool:
    """True if `text` leaks an answer the candidate should be deriving themselves."""
    patterns = _LEAK_PATTERNS.get(track)
    if not patterns:
        return False
    return any(p.search(text) for p in patterns)


def sanitize(draft: str, track: str, regenerate_fn) -> str:
    """
    Returns a safe version of `draft` to show the candidate.

    draft:          the interviewer's first-pass question/response
    track:          interview track — only "technical" and "system-design" are checked
    regenerate_fn:  zero-arg callable that asks the LLM to rewrite `draft` without
                     leaking; may raise, in which case we just fall back
    """
    if not violates(draft, track):
        return draft

    try:
        retry = regenerate_fn()
        if not violates(retry, track):
            return retry
    except Exception:
        pass

    return random.choice(_FALLBACK_QUESTIONS[track])
