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
import threading

from services.supabase_client import get_supabase

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "question_bank.json")
_lock = threading.Lock()
_cache: list[dict] | None = None


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
) -> dict | None:
    """Random question matching track/language(/topic/difficulty). None if nothing
    matches — callers should fall back to ad hoc LLM-generated problems in that case.

    difficulty defaults to ["easy", "medium"] — "hard" problems are excluded unless
    explicitly requested, since they're disproportionately represented in the
    imported LeetCodeDataset batch (71/210) and aren't a great default mock-interview
    experience. Pass difficulty="hard" (or a list including it) once seniority-level
    selection is wired up.
    """
    if difficulty is None:
        difficulty = ["easy", "medium"]
    elif isinstance(difficulty, str):
        difficulty = [difficulty]

    candidates = [
        q for q in _all_questions()
        if q.get("track") == track and language in (q.get("languages") or [])
        and (topic is None or q.get("topic") == topic)
        and (q.get("difficulty") or "medium") in difficulty
    ]
    return random.choice(candidates) if candidates else None


def get_question(question_id: str) -> dict | None:
    return next((q for q in _all_questions() if q.get("id") == question_id), None)
