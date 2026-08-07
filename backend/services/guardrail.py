"""
Guardrail layer — stops the interviewer from leaking the answer inside its own
question. Two concrete leaks we must never let through:

  technical       — stating the time/space complexity of the candidate's (or the
                     optimal) solution instead of asking the candidate to derive it.
  system-design   — declaring a specific architectural decision (which database,
                     caching layer, scaling pattern, etc.) instead of asking the
                     candidate to propose and defend one.

Defense in depth, three layers:
  1. Prompt hardening (see TRACK_PERSONAS in llm.py) — primary defense, cheapest.
  2. Regex detector — catches known patterns with near-zero latency.
  3. LLM judge — fires only when regex passes; fast YES/NO call (max_tokens=3)
     that catches novel phrasing the regex doesn't know about. Tries Groq first,
     then falls back to the same Ollama-cloud provider the main interview chain
     uses (see FALLBACK_BASE_URL/_yes_no_judge below) — otherwise this layer
     would silently go dark for the whole outage window even while the
     interview itself keeps running on the fallback model. Fails open (returns
     False) only if neither provider answers, keeping the guardrail non-blocking.
"""

from __future__ import annotations

import os
import random
import re

from services import metrics, token_meter

_COMPLEXITY_PATTERNS = [
    re.compile(r"O\(\s*[a-zA-Z0-9log\s\*\+\^,]+\s*\)"),
    re.compile(r"\b(time|space)\s+complexity\s+(is|would be)\s+O\(", re.IGNORECASE),
    re.compile(r"\b(time|space)\s+complexity\s+(of (your|this|the)\s+\w+\s+is)\b", re.IGNORECASE),
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
    """Layer 2: fast regex check. True if `text` matches a known leak pattern."""
    patterns = _LEAK_PATTERNS.get(track)
    if not patterns:
        return False
    return any(p.search(text) for p in patterns)


def _chat_completion(
    base_url: str, api_key: str, model: str, prompt: str, provider: str = "groq",
) -> str | None:
    """One YES/NO-judge call against an OpenAI-compatible /chat/completions
    endpoint. Returns the upper-cased reply, or None on any failure (bad
    status, timeout, malformed response) so callers can try the next provider.

    This judge fires on every interviewer turn, so its usage is metered even
    though the reply is only 3 tokens — the OUTPUT is tiny but the INPUT
    carries the whole draft response, which is what actually costs. Leaving it
    unmetered would understate per-session cost by one full call per turn."""
    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 3,
                "temperature": 0,
            },
            timeout=5,
        )
        resp.raise_for_status()
        body = resp.json()
        token_meter.record_openai_usage("guardrail.judge", provider, body)
        return body["choices"][0]["message"]["content"].strip().upper()
    except Exception:
        return None


def _yes_no_judge(prompt: str) -> bool:
    """Fires `prompt` at Groq, falling back to the same Ollama-cloud provider
    services.llm uses (FALLBACK_BASE_URL/FALLBACK_API_KEY/FALLBACK_MODEL) if
    Groq is unreachable — so a Groq outage doesn't silently disable this
    judge layer while the interview itself keeps running on the fallback
    model. Fails open (returns False) only if neither provider answers."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key:
        answer = _chat_completion(
            "https://api.groq.com/openai/v1", groq_key,
            os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"), prompt,
            provider="groq",
        )
        if answer is not None:
            return answer.startswith("YES")

    fallback_base = os.environ.get("FALLBACK_BASE_URL", "")
    fallback_key = os.environ.get("FALLBACK_API_KEY", "")
    if fallback_base and fallback_key:
        answer = _chat_completion(
            fallback_base, fallback_key,
            os.environ.get("FALLBACK_MODEL", "llama3.3:70b"), prompt,
            provider="fallback",
        )
        if answer is not None:
            return answer.startswith("YES")

    return False


def _llm_judge(text: str, track: str) -> bool:
    """Layer 3: LLM judge. Only called when regex passes."""
    prompts = {
        "technical": (
            "Does the following interviewer message reveal the time or space complexity "
            "of any solution (e.g. O(n), O(1), linear time, constant space), or state "
            "that a solution is optimal without asking the candidate to verify? "
            "Reply YES or NO only.\n\n" + text
        ),
        "system-design": (
            "Does the following interviewer message recommend a specific architectural "
            "component (a named database, cache, queue, load balancer, or scaling strategy) "
            "to the candidate instead of asking them to propose and defend one? "
            "Reply YES or NO only.\n\n" + text
        ),
    }
    prompt = prompts.get(track)
    if not prompt:
        return False
    return _yes_no_judge(prompt)


def sanitize(draft: str, track: str, regenerate_fn) -> str:
    """
    Returns a safe version of `draft` to show the candidate.

    draft:          the interviewer's first-pass question/response
    track:          interview track — only "technical" and "system-design" are checked
    regenerate_fn:  zero-arg callable that asks the LLM to rewrite `draft` without
                     leaking; may raise, in which case we just fall back

    Detection order: regex (Layer 2) first for speed; LLM judge (Layer 3) only
    when regex is clean, to catch novel phrasing at low cost.
    """
    layer1 = violates(draft, track)
    layer2 = not layer1 and _llm_judge(draft, track)

    # EVALUATION_METRICS.md §6 called the guardrail "not yet measurable at
    # scale" because nothing recorded when a layer fired versus passed clean.
    # Recorded per layer, not just overall, because the regex and the judge
    # catching different things is the whole argument for having both.
    metrics.record_guardrail(track, "regex", layer1)
    if not layer1:
        metrics.record_guardrail(track, "llm_judge", layer2)

    if not layer1 and not layer2:
        return draft

    try:
        retry = regenerate_fn()
        if not violates(retry, track) and not _llm_judge(retry, track):
            metrics.record_guardrail(track, "regenerate", False)
            return retry
    except Exception:
        pass

    # Both the draft and its rewrite leaked — the candidate gets a canned
    # question instead. This is the layer to alert on: it means the model is
    # ignoring prompt hardening AND failing to correct itself.
    metrics.record_guardrail(track, "safe_fallback", True)
    return random.choice(_FALLBACK_QUESTIONS[track])


# ── Second-problem guard (technical + system-design) ─────────────────────────
# Technical: the code editor, boilerplate, and test harness are tied to
# exactly ONE assigned_question per session. System-design: the candidate's
# diagram is graded at the end against that ONE assigned problem's
# expected_components. Neither track has a mechanism to swap to a new problem
# mid-session. Prompt hardening alone isn't reliable (see TRACK_PERSONAS in
# llm.py) — the model has been observed ignoring it and announcing a second
# "classic challenge" anyway, which silently desyncs the transcript (talking
# about problem B) from whatever's actually being graded (still problem A).
# This is the same defense-in-depth shape as sanitize() above, just for a
# different failure mode.

_NEW_PROBLEM_PATTERNS = [
    re.compile(r"\blet'?s\s+(move on|switch|try|design)\s+(to|another|a\s+different)\b", re.IGNORECASE),
    re.compile(r"\b(another|a new|a different|the next)\s+(coding\s+|technical\s+|system[\s-]?design\s+)?(problem|challenge|question|system)\b", re.IGNORECASE),
    re.compile(r"^\s*\**problem\**\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bnext\s+(problem|challenge)\b", re.IGNORECASE),
    re.compile(r"\blet'?s\s+design\s+(a|an)\b", re.IGNORECASE),
]


def introduces_new_problem(text: str) -> bool:
    """Layer 2: fast regex check for phrasing that hands the candidate a
    second problem instead of following up on the one already assigned."""
    return any(p.search(text) for p in _NEW_PROBLEM_PATTERNS)


def _llm_judge_new_problem(text: str) -> bool:
    """Layer 3: LLM judge, only called when regex is clean."""
    prompt = (
        "Does the following interviewer message try to introduce or assign a NEW problem "
        "(coding problem or system design problem) to the candidate, as opposed to following "
        "up on a problem already given (discussing approach, complexity, trade-offs, scale, "
        "failure modes, etc.)? Reply YES or NO only.\n\n" + text
    )
    return _yes_no_judge(prompt)


def sanitize_no_new_problem(draft: str, regenerate_fn) -> str:
    """Same shape as sanitize(), scoped to the 'introduces a second coding
    problem' failure mode. Callers gate this themselves — only relevant once a
    technical problem has already been assigned (not on the turn it's first
    presented, which legitimately looks like 'introducing a problem')."""
    layer1 = introduces_new_problem(draft)
    layer2 = not layer1 and _llm_judge_new_problem(draft)

    if not layer1 and not layer2:
        return draft

    try:
        retry = regenerate_fn()
        if not introduces_new_problem(retry) and not _llm_judge_new_problem(retry):
            return retry
    except Exception:
        pass

    return (
        "Let's keep digging into the problem you're already working on — what's the time "
        "complexity of your current approach, and do you see any way to improve it?"
    )


# ── Candidate-initiated question switch (technical track) ────────────────────
# The one deliberate exception to the guard above: the CANDIDATE explicitly
# asking for a different problem is a real signal the interviewer-side
# guardrail can't distinguish from itself going off script, so it's detected
# here instead and handled by routers.interview.post_message reassigning
# session["assigned_question"] and passing is_new_assignment=True for that
# turn — same mechanism the very first question uses, just candidate-driven
# instead of automatic.

_CANDIDATE_NEW_PROBLEM_PATTERNS = [
    re.compile(r"\bnext\s+(question|problem|dsa|challenge)\b", re.IGNORECASE),
    re.compile(r"\b(another|a\s+new|a\s+different)\s+(question|problem|challenge)\b", re.IGNORECASE),
    re.compile(r"\bcan\s+(i|we)\s+(get|have|move\s+on\s+to)\s+(another|a\s+new|the\s+next)\b", re.IGNORECASE),
    re.compile(r"\b(skip|change)\s+(this\s+)?(question|problem)\b", re.IGNORECASE),
    re.compile(r"\bgive\s+me\s+(another|a\s+new|the\s+next)\s+(question|problem|challenge)\b", re.IGNORECASE),
]


def candidate_requests_new_problem(text: str) -> bool:
    """True if the CANDIDATE's message is asking to switch to a different
    problem — e.g. 'next question please', 'can I get a different problem'.
    Deliberately regex-only (no LLM judge): false negatives here just mean a
    normal follow-up turn happens instead, which is always a safe fallback,
    so there's no need to pay for a judge call on every single message."""
    return any(p.search(text) for p in _CANDIDATE_NEW_PROBLEM_PATTERNS)
