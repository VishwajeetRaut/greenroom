"""
LLM analysis of a pasted job description.

What this replaces
------------------
Before this module, a job description did exactly two things: it was appended
verbatim to the interviewer's system prompt, and nothing else. In particular
it never reached question selection — `question_generator` was called with
only `(role, candidate_intro)`, so a JD for a "Senior Distributed Systems
Engineer, Go, Kafka, heavy on graph algorithms" produced the same randomly
chosen array problem as a blank JD.

Worse, the `role` those prompts interpolated was the hardcoded string
"Software Engineer" — set in the frontend (`useInterviewSession.js`) and
defaulted the same way in `StartSessionRequest`. Every persona, every question
selection, and every evaluation was written for a generic SWE regardless of
what the candidate actually pasted.

This module turns the JD into a structured profile once, at session start, and
that profile then drives the role string, question topic, and difficulty.

Constrained vocabulary
----------------------
The model does not invent topic names. The question bank's `topic` field is a
fixed and frankly inconsistent vocabulary ("hash-table" but "binary search",
"dynamic-programming" but "data structures"), so free-text topics would match
nothing and silently degrade to a random pick. Instead the real vocabulary is
read out of the bank and shown to the model, and anything it returns that
isn't in that list is dropped. Same principle as
`question_generator._build_catalog`: constrain to real values, then validate
anyway.

Failure is soft by construction
-------------------------------
Every failure path returns None, and every caller treats None as "no JD
profile" — which is exactly the behaviour that existed before this module. A
bad or unparseable analysis can degrade question selection back to random; it
can never block a session from starting.
"""

from __future__ import annotations

import json
import re
from typing import Any

from services import llm_cache, question_bank
from services.logger import log

# Seniority is a closed set because it maps onto the bank's difficulty tiers.
# Anything outside it is treated as unknown rather than guessed at.
_SENIORITY_DIFFICULTY = {
    "junior": ["easy", "medium"],
    "mid": ["easy", "medium"],
    "senior": ["medium", "hard"],
    "staff": ["medium", "hard"],
}

_MAX_TOPICS = 6
_MAX_STACK = 12

_SYSTEM = """\
You extract structured facts from a software engineering job description, to \
tailor a mock interview to it.

Reply ONLY as valid JSON, no markdown fences, exactly this shape:
{{
  "role_title": "<the actual role title, e.g. 'Senior Backend Engineer'. If the \
description doesn't state one, infer the most specific title it supports.>",
  "seniority": "junior" | "mid" | "senior" | "staff",
  "tech_stack": ["<technology named in the description, lowercase>", ...],
  "technical_topics": ["<topic>", ...],
  "behavioral_themes": ["<theme>", ...],
  "system_design_topics": ["<topic>", ...],
  "focus_summary": "<one sentence: what this interview should probe hardest>"
}}

"technical_topics", "behavioral_themes" and "system_design_topics" MUST be \
chosen ONLY from the lists below — copy the strings verbatim, do not invent \
new ones, do not reword them. Pick at most {max_topics} of each, ordered most \
relevant first. Return an empty list if nothing in a list genuinely fits — an \
empty list is much better than a bad match.

Allowed technical_topics:
{technical_topics}

Allowed behavioral_themes:
{behavioral_themes}

Allowed system_design_topics:
{system_design_topics}

"tech_stack" is free text (it is only used to colour the interviewer's \
phrasing, never to look anything up): list technologies the description \
actually names, lowercase, at most {max_stack}."""


def _bank_topics(track: str) -> list[str]:
    """The topic vocabulary actually present in the bank, so the model is only
    ever offered values that can match a real question."""
    topics = {
        q.get("topic") for q in question_bank._all_questions()
        if q.get("track") == track and q.get("topic")
    }
    return sorted(topics)


def _clean_list(value: Any, allowed: set[str] | None, limit: int) -> list[str]:
    """Keeps only strings, de-duplicates preserving order, and — when a
    vocabulary is supplied — drops anything the model invented."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item:
            continue
        if allowed is not None and item not in allowed:
            continue
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```[a-z]*\n?", "", text.strip())
    return re.sub(r"\n?```$", "", text).strip()


def _analyze_uncached(job_description: str) -> dict | None:
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.llm import _fallback_chat, _make_llm

    technical = _bank_topics("technical")
    behavioral = _bank_topics("behavioral")
    system_design = _bank_topics("system-design")

    system = _SYSTEM.format(
        max_topics=_MAX_TOPICS,
        max_stack=_MAX_STACK,
        technical_topics=", ".join(technical) or "(none)",
        behavioral_themes=", ".join(behavioral) or "(none)",
        system_design_topics=", ".join(system_design) or "(none)",
    )
    user = f"Job description:\n\n{job_description.strip()}"

    try:
        chat = _make_llm(temperature=0.1, max_tokens=600, call_site="jd_analyzer")
        raw = chat.invoke([SystemMessage(content=system), HumanMessage(content=user)]).content
    except Exception as exc:
        try:
            raw = _fallback_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=600, temperature=0.1, json_mode=True, call_site="jd_analyzer",
            )
        except Exception:
            log.warning("jd_analyzer.failed", error=str(exc))
            return None

    try:
        spec = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        log.warning("jd_analyzer.unparseable")
        return None
    if not isinstance(spec, dict):
        return None

    seniority = spec.get("seniority")
    if seniority not in _SENIORITY_DIFFICULTY:
        seniority = None

    role_title = spec.get("role_title")
    role_title = role_title.strip() if isinstance(role_title, str) else ""
    focus = spec.get("focus_summary")

    profile = {
        # Falls back to the same default StartSessionRequest uses, so a JD the
        # model couldn't title still behaves exactly as it did before.
        "role_title": role_title[:100] or "Software Engineer",
        "seniority": seniority,
        "difficulty": _SENIORITY_DIFFICULTY.get(seniority or "", None),
        "tech_stack": _clean_list(spec.get("tech_stack"), None, _MAX_STACK),
        "technical_topics": _clean_list(spec.get("technical_topics"), set(_bank_topics("technical")), _MAX_TOPICS),
        "behavioral_themes": _clean_list(spec.get("behavioral_themes"), set(_bank_topics("behavioral")), _MAX_TOPICS),
        "system_design_topics": _clean_list(spec.get("system_design_topics"), set(_bank_topics("system-design")), _MAX_TOPICS),
        "focus_summary": focus.strip()[:300] if isinstance(focus, str) else "",
    }
    log.info(
        "jd_analyzer.ok",
        role_title=profile["role_title"],
        seniority=profile["seniority"],
        technical_topics=profile["technical_topics"],
    )
    return profile


def analyze(job_description: str | None) -> dict | None:
    """Structured profile for a job description, or None if there's nothing to
    analyze or the analysis failed.

    Cached on the JD text (services.llm_cache): the analysis is a pure
    function of the description at temperature 0.1, and the same posting is
    routinely pasted by many candidates — and by the same candidate across
    repeat sessions.
    """
    if not job_description or not job_description.strip():
        return None
    text = job_description.strip()
    return llm_cache.cached_call(
        "jd_analyzer",
        (_SYSTEM, text),
        lambda: _analyze_uncached(text),
        prompt_chars=len(_SYSTEM) + len(text),
    )


def topics_for_track(profile: dict | None, track: str) -> list[str]:
    if not profile:
        return []
    return profile.get({
        "technical": "technical_topics",
        "behavioral": "behavioral_themes",
        "system-design": "system_design_topics",
    }.get(track, ""), []) or []


def difficulty_for(profile: dict | None) -> list[str] | None:
    """None means "caller keeps its own default" — an unknown seniority must
    not silently narrow the pool."""
    return (profile or {}).get("difficulty")


def prompt_fragment(profile: dict | None) -> str:
    """Compact, structured summary for the interviewer's system prompt.

    Replaces dumping the raw JD (up to 5000 chars) into every single turn's
    prompt. That mattered more than it looks: the interviewer prompt is
    re-sent on every turn, so a long pasted JD was being paid for N times per
    session — and it's the largest single cost in a session (see
    docs/MODEL_COST_MATRIX.md).
    """
    if not profile:
        return ""
    parts = [f"Role: {profile['role_title']}"]
    if profile.get("seniority"):
        parts.append(f"Seniority: {profile['seniority']}")
    if profile.get("tech_stack"):
        parts.append(f"Stack: {', '.join(profile['tech_stack'])}")
    if profile.get("focus_summary"):
        parts.append(f"Focus: {profile['focus_summary']}")
    return "\n\n[The role the candidate is interviewing for]\n" + "\n".join(parts)
