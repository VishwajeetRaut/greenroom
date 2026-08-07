"""
Question bank — curated, verified technical interview problems with canonical
test cases (the file these came from, data/question_bank.json, is checked by
data/verify_question_bank.py against reference solutions, so every expected
value is actually correct rather than LLM-guessed).

Update path: this reads from a `questions` table in Supabase first, falling
back to the local JSON seed if that table is empty or unreachable. That means
the bank can grow or change at any time — add a row in Supabase — without a
backend redeploy. The local JSON file is only the bootstrap seed; the durable,
"keeps updating" copy lives in Supabase.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading

from services.supabase_client import get_supabase

_CLASS_METHOD_PATTERN = re.compile(r"^(\w+)\(\)\.(\w+)$")


def parse_function_name(raw: str | None) -> tuple[str | None, str]:
    """Splits a question's function_name field into (class_name, method_name).

    Most entries are plain functions, e.g. "two_sum" -> (None, "two_sum").
    LeetCode-imported entries instead encode the calling convention as
    "Solution().methodName" -> ("Solution", "methodName") — that exact string
    is required verbatim by test_runner, which executes tests["call"] as-is
    (e.g. "Solution().longestPalindromicSubsequence(s='a', k=2)"), so it is
    NOT a data bug to fix — the class *is* part of how these are invoked.
    Callers that need to talk about the signature (the interviewer's phrasing,
    generated boilerplate) should use the parsed method_name instead of the
    raw field, since "Solution().methodName" is not a valid identifier on its
    own."""
    if not raw:
        return None, ""
    m = _CLASS_METHOD_PATTERN.match(raw)
    if m:
        return m.group(1), m.group(2)
    return None, raw

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "question_bank.json")
_lock = threading.Lock()
_cache: list[dict] | None = None

_JUNIOR_KEYWORDS = ("junior", "jr.", "jr ", "entry", "intern", "associate", "new grad", "graduate", "trainee")
_SENIOR_KEYWORDS = ("senior", "sr.", "sr ", "staff", "principal", "lead", "architect", "distinguished")

# Difficulty mix per seniority tier — junior interviews skew easy with fewer
# mediums and no hards; senior interviews skew toward medium/hard and rarely
# open with easy. "mid" (the default, unlabeled role) stays close to the
# original uniform-ish behavior.
_DIFFICULTY_WEIGHTS = {
    "junior": {"easy": 0.65, "medium": 0.35, "hard": 0.0},
    "mid":    {"easy": 0.35, "medium": 0.45, "hard": 0.20},
    "senior": {"easy": 0.10, "medium": 0.45, "hard": 0.45},
}


def infer_seniority(role: str | None) -> str:
    """Buckets a free-text role string (e.g. "Junior Backend Engineer",
    "Staff Software Engineer") into "junior" | "mid" | "senior" so question
    selection can skew its difficulty mix accordingly. Defaults to "mid" for
    anything that doesn't mention a seniority keyword."""
    role_lower = (role or "").lower()
    if any(kw in role_lower for kw in _JUNIOR_KEYWORDS):
        return "junior"
    if any(kw in role_lower for kw in _SENIOR_KEYWORDS):
        return "senior"
    return "mid"


def _weighted_choice(candidates: list[dict], seniority: str) -> dict | None:
    """Picks one candidate, weighting by difficulty per _DIFFICULTY_WEIGHTS
    instead of a flat random.choice — falls back to uniform choice if the
    weighted pool is empty (e.g. every candidate's difficulty has weight 0)."""
    if not candidates:
        return None
    weights = _DIFFICULTY_WEIGHTS[seniority]
    scored = [(c, weights.get(c.get("difficulty") or "medium", 0)) for c in candidates]
    total = sum(w for _, w in scored)
    if total <= 0:
        return random.choice(candidates)
    r = random.uniform(0, total)
    upto = 0.0
    for c, w in scored:
        upto += w
        if upto >= r:
            return c
    return scored[-1][0]


def _load_seed() -> list[dict]:
    with open(_SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_from_supabase() -> list[dict] | None:
    sb = get_supabase()
    if not sb:
        return None
    try:
        resp = sb.table("questions").select("*").execute()
        return resp.data or None
    except Exception:
        return None


def _all_questions() -> list[dict]:
    """Cached for the life of the process; call refresh() to force a re-read
    (e.g. after editing the Supabase table) without restarting the backend."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_supabase() or _load_seed()
        return _cache


def refresh() -> None:
    global _cache
    with _lock:
        _cache = None


def pick_question(
    track: str,
    language: str = "python",
    topic: str | None = None,
    difficulty: str | list[str] | None = None,
    exclude_ids: set[str] | None = None,
    role: str | None = None,
) -> dict | None:
    """Random question matching track/language(/topic/difficulty). None if nothing
    matches — callers should fall back to ad hoc LLM-generated problems in that case.

    difficulty defaults to ["easy", "medium"] — "hard" problems are excluded unless
    explicitly requested, since they're disproportionately represented in the
    imported LeetCodeDataset batch (71/210) and aren't a great default mock-interview
    experience, EXCEPT for senior roles (see `role` below), where hard is included.

    role: free-text role string (e.g. "Junior Backend Engineer"). When
    `difficulty` is not explicitly pinned by the caller, this is used to skew
    the pick's difficulty mix — more easy for junior roles, more medium/hard
    for senior roles — via infer_seniority()/_weighted_choice() instead of a
    flat random.choice.

    exclude_ids: question ids to skip — e.g. ones already assigned earlier in
    the same session (see "Next question" in routers/interview.py), so asking
    for another problem doesn't risk re-serving the one just finished.
    """
    explicit_difficulty = difficulty is not None
    seniority = infer_seniority(role)
    if difficulty is None:
        difficulty = ["easy", "medium", "hard"] if seniority == "senior" else ["easy", "medium"]
    elif isinstance(difficulty, str):
        difficulty = [difficulty]

    candidates = [
        q for q in _all_questions()
        if q.get("track") == track and language in (q.get("languages") or [])
        and (topic is None or q.get("topic") == topic)
        and (q.get("difficulty") or "medium") in difficulty
        and q["id"] not in (exclude_ids or set())
    ]
    if not candidates:
        return None
    if explicit_difficulty:
        return random.choice(candidates)
    return _weighted_choice(candidates, seniority)


def get_question(question_id: str) -> dict | None:
    return next((q for q in _all_questions() if q.get("id") == question_id), None)


def pick_behavioral_question(
    topic: str | None = None, difficulty: str | list[str] | None = None, role: str | None = None,
) -> dict | None:
    """Random behavioral question, optionally filtered by topic and difficulty.

    role: when `difficulty` isn't explicitly pinned, skews the pick's
    difficulty mix by seniority (see pick_question)."""
    explicit_difficulty = difficulty is not None
    if isinstance(difficulty, str):
        difficulty = [difficulty]
    candidates = [
        q for q in _all_questions()
        if q.get("track") == "behavioral"
        and (topic is None or q.get("topic") == topic)
        and (difficulty is None or (q.get("difficulty") or "medium") in difficulty)
    ]
    if not candidates:
        return None
    if explicit_difficulty:
        return random.choice(candidates)
    return _weighted_choice(candidates, infer_seniority(role))


_SCALE_FIELD_LABELS = {
    "daily_active_users": "Daily active users",
    "writes_per_day": "Writes/day",
    "reads_per_day": "Reads/day",
    "peak_qps": "Peak QPS",
    "data_volume": "Data volume",
    "latency_slo": "Latency SLO",
}


def scale_for(question: dict, tier: str | None) -> dict | None:
    """The scale numbers to run this system-design question at.

    Falls back to the question's own native difficulty when `tier` is None or
    isn't defined for this question — so a question that only has metadata for
    the tier it was authored at still works, and a question with no
    `scale_tiers` at all returns None and the caller keeps using the authored
    `constraints`. Both are ordinary, not error cases.
    """
    tiers = question.get("scale_tiers") or {}
    if not tiers:
        return None
    return tiers.get(tier) or tiers.get(question.get("difficulty") or "medium") or None


def format_scale(scale: dict | None) -> list[str]:
    """Scale dict rendered as constraint-style lines, in a stable field order
    (dict order comes from whatever the generator emitted, which varies)."""
    if not scale:
        return []
    return [
        f"{label}: {scale[field]}"
        for field, label in _SCALE_FIELD_LABELS.items()
        if scale.get(field)
    ]


def pick_system_design_question(
    topic: str | None = None, difficulty: str | list[str] | None = None,
    role: str | None = None, tags: list[str] | None = None,
) -> dict | None:
    """Random system-design question. Includes all difficulties by default (unlike
    pick_question which excludes hard). None if nothing matches.

    role: when `difficulty` isn't explicitly pinned, skews the pick's
    difficulty mix by seniority (see pick_question).

    topic/tags: narrow the pool to what a pasted job description actually
    calls for (see services.jd_analyzer). Deliberately kept separate from `role`:
    seniority controls HOW HARD the question is via the weighting below, and
    topic controls WHICH question it is. Filtering on difficulty here as well
    would double-apply seniority and starve an already-uneven pool.
    """
    explicit_difficulty = difficulty is not None
    if isinstance(difficulty, str):
        difficulty = [difficulty]
    wanted_tags = set(tags or [])
    candidates = [
        q for q in _all_questions()
        if q.get("track") == "system-design"
        and (topic is None or q.get("topic") == topic)
        and (difficulty is None or (q.get("difficulty") or "medium") in difficulty)
        # Any-match, not all-match: tags describe characteristics, and requiring
        # every one of them would almost always return nothing.
        and (not wanted_tags or wanted_tags & set(q.get("tags") or []))
    ]
    if not candidates:
        return None
    if explicit_difficulty:
        return random.choice(candidates)
    return _weighted_choice(candidates, infer_seniority(role))

