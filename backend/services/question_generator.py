"""
LLM-driven question selection/generation — the interviewer sees the actual
catalog of questions already in the bank (services.question_bank) and decides,
in a single LLM call, whether an existing one fits this candidate or whether a
fresh problem is worth generating. This is the credit-conscious design:

  - The common case (an existing problem fits) costs exactly ONE LLM call and
    zero sandbox runs — picking from the bank is free beyond that.
  - Generating a new problem costs that same one call (decision + the new
    problem + ONE reference solution + the LLM's own claimed outputs), then
    N sandbox runs (services.piston — not LLM credits) to get the ACTUAL,
    verified output per input. The LLM's claimed outputs are only used as a
    free cross-check signal against that ground truth, not trusted directly.
  - A second, independently-prompted solution (one more LLM call) is only
    requested when a generated problem is good enough to be worth persisting
    into the shared bank for future sessions — i.e. the extra rigor (and
    extra credit spend) is reserved for the case with a durable payoff,
    not paid on every generation.

This is the same "never trust the LLM's stated expected output, only trust
sandboxed execution" principle used by the LeetCodeDataset/CodeContests
importers (scripts/import_*.py) — just applied to LLM-generated problems
instead of dataset-derived ones.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import uuid

from services import piston, question_bank

_DIFFICULTY_GUIDANCE = {
    "junior": (
        '"easy" | "medium"',
        "This is a JUNIOR-level role: strongly bias toward \"easy\" problems, with only an "
        "occasional \"medium\" — never \"hard\".",
    ),
    "mid": (
        '"easy" | "medium" | "hard"',
        "This role has no clear seniority signal: prefer \"easy\" or \"medium\"; reserve "
        "\"hard\" for a candidate whose intro clearly signals strong, senior-level experience.",
    ),
    "senior": (
        '"medium" | "hard"',
        "This is a SENIOR-level role: strongly bias toward \"medium\" and \"hard\" problems — "
        "only fall back to \"easy\" if the candidate's intro suggests they'd struggle otherwise.",
    ),
}

_DECIDE_OR_GENERATE_SYSTEM = """\
You are choosing the coding problem for a technical interview for a {role} role.

{difficulty_note}

Candidate's introduction (use this to pick something that actually fits their background — \
don't default to the same generic crowd-pleaser every time; choose what's genuinely the best \
match for THIS candidate, including their stated interests, stack, and experience level):
\"\"\"{candidate_intro}\"\"\"

Below is the catalog of problems already available (id | topic | difficulty | title), in \
randomized order. These are pre-verified to run correctly in Python, JavaScript, Java, AND C++ — \
generated problems only reliably support Python/JavaScript (Java/C++ execution for a generated \
problem is unverified and noticeably slower for the candidate). With a catalog this large \
(hundreds of problems spanning most common topics/difficulties), it is extremely rare for none \
of them to reasonably fit — default to "use_existing" and only choose "generate" if you have \
genuinely searched the catalog and nothing is even a reasonable approximate match for this \
candidate's stated background/topic. When in doubt, reuse.

Catalog:
{catalog}

Reply ONLY as valid JSON, no markdown fences, with ONE of these two shapes:

To reuse an existing problem:
{{"action": "use_existing", "id": "<exact id from the catalog>"}}

To generate a new one (only if truly nothing fits):
{{
  "action": "generate",
  "title": "<short title>",
  "topic": "<one or two word topic>",
  "difficulty": {difficulty_options},
  "prompt": "<full problem statement, LeetCode-style: candidate implements a single function \
that takes arguments and returns a value — do NOT ask them to read from stdin or write a full \
program>",
  "function_name": "<snake_case function name, e.g. two_sum>",
  "solution_python": "<a complete, correct Python 3 function definition named function_name \
that solves it — just the function, not a full program>",
  "calls": ["<call expression using function_name, e.g. two_sum([2, 7, 11, 15], 9)>", ...at \
least 5 calls, the last 2 covering edge cases>],
  "claimed_outputs": ["<the value you believe call 1 returns, as a Python literal>", ...],
  "constraints": ["<one constraint per entry, LeetCode-style, e.g. '2 <= nums.length <= 10^4'>", \
...at least 2-3 covering input size/range/uniqueness assumptions>],
  "examples": [
    {{"input": "<a call expression from \\"calls\\" above, verbatim>", "output": "<its expected \
result as a readable literal>", "explanation": "<one sentence, or \\"\\" if not needed>"}},
    ...2 of the entries from "calls", written up as worked examples
  ]
}}
"claimed_outputs" must have exactly one entry per "calls" entry, in the same order. Every \
"examples" input must be copied verbatim from "calls" — do not invent new call expressions here."""

_SECOND_SOLUTION_SYSTEM = """\
You are given a coding problem. Write a complete, correct Python 3 function definition named \
{function_name} that solves it — just the function, not a full program, no reading from stdin. \
Write your own independent implementation — do not assume any particular algorithm. Reply with \
ONLY the Python code, no markdown fences, no explanation."""

_MAX_CATALOG_ENTRIES = 200
_TOPIC_PERSIST_CAP = 25  # don't keep stacking generated problems onto an already-deep topic

# Two Sum specifically is the single most overrepresented "safe default" in
# every LLM's training data — empirically, it gets picked for almost any
# generic-ish candidate intro regardless of prompt wording telling the model
# to personalize (confirmed against real production sessions: "I'm a Java
# SWE, give me a question" still produced Two Sum). A word-count/signal
# heuristic on the intro can't fix this — the bias holds even with some real
# content. The only reliable fix is structural: don't let the model choose it.
# It's still fully reachable via the random bank picker (_has_real_signal
# gate above) and via direct reuse if a candidate explicitly asks for it by
# name in conversation.
_EXCLUDED_FROM_AUTO_PICK = {"two-sum"}


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "problem"
    return f"gen-{base}-{uuid.uuid4().hex[:6]}"


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").strip().splitlines())


def _build_catalog(questions: list[dict]) -> str:
    # Shuffled (not the bank's natural/insertion order) — LLMs exhibit position
    # bias toward earlier list entries, which is exactly why every call was
    # returning the same problem regardless of candidate context.
    sample = random.sample(questions, min(_MAX_CATALOG_ENTRIES, len(questions)))
    return "\n".join(f"{q['id']} | {q.get('topic', 'general')} | {q.get('difficulty', 'medium')} | {q['title']}" for q in sample)


async def _run_solution(source: str, calls: list[str]) -> list[str] | None:
    """Runs `source` (a function definition) against every call expression through
    the real sandbox (never exec()'d in this process) — one call per line of
    stdout, in order. Returns None on any crash. This is function-call
    verification, not stdin/stdout: the candidate implements a function, the
    same shape as the LeetCode-derived bank entries, so boilerplate/signature
    generation works for these exactly like it does for bank questions."""
    calls_json = json.dumps(calls)
    harness = f'''{source}

import json as _j
_calls = {calls_json}
for _c in _calls:
    try:
        print(_j.dumps(repr(eval(_c))))
    except Exception as _e:
        print(_j.dumps(f"ERROR: {{_e}}"))
'''
    result = await piston.run_code("python", "3.10.0", harness, stdin="")
    raw = result.get("run", {})
    if raw.get("stderr") and raw.get("code", 0) != 0:
        return None
    lines = [ln for ln in raw.get("stdout", "").splitlines() if ln.strip()]
    if len(lines) != len(calls):
        return None
    try:
        return [json.loads(ln) for ln in lines]
    except json.JSONDecodeError:
        return None


def _ask_llm(system: str, user: str, temperature: float, max_tokens: int,
             call_site: str = "question_generator") -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.llm import _make_llm
    llm = _make_llm(temperature=temperature, max_tokens=max_tokens, call_site=call_site)
    result = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return _strip_fences(result.content)


def _ask_llm_fallback(system: str, user: str, temperature: float, max_tokens: int,
                      call_site: str = "question_generator") -> str:
    from services.llm import _fallback_chat
    return _strip_fences(_fallback_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens, temperature, call_site=call_site,
    ))


_MIN_INTRO_WORDS = 6  # below this, there's no real signal to reason about


def _has_real_signal(candidate_intro: str) -> bool:
    """True if the intro actually says something about the candidate's
    background, as opposed to a content-free request like "give me my first
    DSA question" or "next". Below this bar, asking the LLM to "pick what
    fits this candidate" gives it nothing to differentiate on, and it
    reliably converges on the same "obvious" answer (Two Sum) every time —
    confirmed empirically, not theoretical. Skipping straight to the
    genuinely-random bank picker in that case is both cheaper (no LLM call)
    and actually delivers the variety the LLM path was supposed to provide."""
    words = [w for w in re.findall(r"[a-zA-Z']+", candidate_intro) if len(w) > 2]
    return len(words) >= _MIN_INTRO_WORDS


async def select_or_generate_question(
    role: str, candidate_intro: str = "", exclude_ids: set[str] | None = None,
) -> dict | None:
    """Main entry point. Returns a question dict (bank-shaped) or None if even
    the existing-bank fallback has nothing for this track — callers should
    treat None the same as "no canonical problem available".

    candidate_intro: the candidate's actual introduction/background, if available.
    Without real per-candidate context the model has nothing to differentiate
    sessions on and tends to converge on the same "obvious" answer every time —
    pass this whenever you have it (i.e. call this after the candidate's first
    reply, not at session start before they've said anything).

    exclude_ids: questions already assigned earlier in this session (see
    "Next question" in routers/interview.py) — skipped everywhere so
    requesting another problem can't just re-serve the one just finished."""
    exclude_ids = exclude_ids or set()
    fallback_pick = lambda: question_bank.pick_question(  # noqa: E731
        "technical", language="python", exclude_ids=exclude_ids, role=role,
    )

    all_questions = await asyncio.to_thread(question_bank._all_questions)
    technical = [q for q in all_questions if q.get("track") == "technical" and q["id"] not in exclude_ids]
    if not technical:
        return None

    if not _has_real_signal(candidate_intro):
        picked = await asyncio.to_thread(fallback_pick)
        if picked:
            return picked
        # Fall through to the LLM path only if the random picker somehow
        # found nothing (bank empty/misconfigured) — better than returning
        # None outright and leaving the candidate with no problem at all.

    catalog_pool = [q for q in technical if q["id"] not in _EXCLUDED_FROM_AUTO_PICK]
    catalog = _build_catalog(catalog_pool)
    difficulty_options, difficulty_note = _DIFFICULTY_GUIDANCE[question_bank.infer_seniority(role)]
    system = _DECIDE_OR_GENERATE_SYSTEM.format(
        role=role, candidate_intro=candidate_intro or "(not provided)", catalog=catalog,
        difficulty_options=difficulty_options, difficulty_note=difficulty_note,
    )
    user_msg = "Choose now."

    try:
        raw = await asyncio.to_thread(_ask_llm, system, user_msg, 1.0, 1200, "question_generator.select")
    except Exception:
        try:
            raw = await asyncio.to_thread(_ask_llm_fallback, system, user_msg, 1.0, 1200, "question_generator.select")
        except Exception:
            return fallback_pick()

    try:
        spec = json.loads(raw)
        action = spec.get("action")
    except json.JSONDecodeError:
        return fallback_pick()

    if action == "use_existing":
        picked_id = spec.get("id", "")
        if picked_id in _EXCLUDED_FROM_AUTO_PICK or picked_id in exclude_ids:
            # Shouldn't happen (it's not in the catalog we showed), but the
            # model can hallucinate an id it never saw — don't trust it blind.
            return fallback_pick()
        found = question_bank.get_question(picked_id)
        return found or fallback_pick()

    if action != "generate":
        return fallback_pick()

    try:
        title, topic, difficulty = spec["title"], spec["topic"], spec["difficulty"]
        prompt, solution, calls = spec["prompt"], spec["solution_python"], spec["calls"]
        function_name = spec["function_name"]
        claimed = spec.get("claimed_outputs") or []
        if not calls or len(calls) < 3 or not function_name:
            return fallback_pick()
    except (KeyError, TypeError):
        return fallback_pick()

    actual_outputs = await _run_solution(solution, calls)
    if actual_outputs is None:
        return fallback_pick()

    # Cross-check the LLM's own claimed outputs against sandbox ground truth —
    # free (no extra LLM call), catches the LLM narrating a different result
    # than what its own code actually does. The sandbox result is what we keep
    # either way; a high mismatch rate means this problem isn't trustworthy.
    if claimed and len(claimed) == len(actual_outputs):
        mismatches = sum(1 for c, a in zip(claimed, actual_outputs) if _normalize(c) != _normalize(a))
        if mismatches > 1:
            return fallback_pick()

    tests = [{"call": c, "expected": o} for c, o in zip(calls, actual_outputs)]

    # constraints/examples are cosmetic (never used for grading), so a
    # malformed or missing value here shouldn't sink an otherwise-verified
    # question — fall back to empty rather than rejecting it.
    constraints = spec.get("constraints") or []
    if not isinstance(constraints, list):
        constraints = []
    raw_examples = spec.get("examples") or []
    valid_calls = set(calls)
    examples = [
        {"input": e.get("input", ""), "output": e.get("output", ""), "explanation": e.get("explanation", "")}
        for e in raw_examples
        if isinstance(e, dict) and e.get("input") in valid_calls
    ]

    question = {
        "id": _slugify(title),
        "track": "technical",
        "topic": topic,
        "difficulty": difficulty,
        "title": title,
        "prompt": prompt,
        "function_name": function_name,
        "languages": ["python"],
        "tests": tests,
        "visible_count": min(3, len(tests)),
        "constraints": constraints,
        "examples": examples,
    }

    # Persistence is the expensive, durable commitment (every future candidate
    # may get this problem), so it gets the heavier dual-solution bar — but
    # only if it clears a cheap, free uniqueness check first.
    topic_count = sum(1 for q in technical if q.get("topic") == topic)
    title_collision = any(q["title"].strip().lower() == title.strip().lower() for q in technical)
    if not title_collision and topic_count < _TOPIC_PERSIST_CAP:
        asyncio.create_task(_verify_and_persist(question, prompt, function_name, calls, actual_outputs))

    return question


async def _verify_and_persist(
    question: dict, prompt: str, function_name: str, calls: list[str], primary_outputs: list[str],
) -> None:
    """Runs in the background (doesn't block the candidate's session) — gets a
    second, independently-prompted solution and only writes to Supabase if it
    agrees with the primary solution on every call."""
    try:
        system = _SECOND_SOLUTION_SYSTEM.format(function_name=function_name)
        solution_b = await asyncio.to_thread(_ask_llm, system, prompt, 0.5, 800, "question_generator.second_solution")
    except Exception:
        return

    outputs_b = await _run_solution(solution_b, calls)
    if outputs_b is None:
        return
    if any(_normalize(a) != _normalize(b) for a, b in zip(primary_outputs, outputs_b)):
        return  # independent solutions disagree — don't trust either, don't persist

    await asyncio.to_thread(_persist_question, question)


def _persist_question(question: dict) -> None:
    from services.supabase_client import get_supabase
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("questions").insert({
            "id": question["id"],
            "track": question["track"],
            "topic": question["topic"],
            "difficulty": question["difficulty"],
            "title": question["title"],
            "prompt": question["prompt"],
            "function_name": question["function_name"],
            "languages": question["languages"],
            "tests": question["tests"],
            "constraints": question.get("constraints") or [],
            "examples": question.get("examples") or [],
        }).execute()
        question_bank.refresh()
    except Exception:
        pass
