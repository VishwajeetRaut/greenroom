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

_DECIDE_OR_GENERATE_SYSTEM = """\
You are choosing the coding problem for a technical interview for a {role} role.

Candidate's introduction (use this to pick something that actually fits their background — \
don't default to the same generic crowd-pleaser every time; choose what's genuinely the best \
match for THIS candidate, including their stated interests, stack, and experience level):
\"\"\"{candidate_intro}\"\"\"

Below is the catalog of problems already available (id | topic | difficulty | title), in \
randomized order. Prefer reusing one of these — they're already verified and free to serve. \
Only generate a new problem if nothing in the catalog is a good fit for this candidate.

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
  "difficulty": "easy" | "medium",
  "prompt": "<full problem statement for stdin/stdout judging: candidate writes a complete \
program that reads input from stdin and prints the answer to stdout>",
  "solution_python": "<a complete, correct Python 3 program solving it>",
  "inputs": ["<raw stdin text>", "<raw stdin text>", "<raw stdin text>", "<edge case stdin>", "<edge case stdin>"],
  "claimed_outputs": ["<output you believe input 1 produces>", ...]
}}
"claimed_outputs" must have exactly one entry per "inputs" entry, in the same order."""

_SECOND_SOLUTION_SYSTEM = """\
You are given a coding problem. Write a complete, correct Python 3 program that solves it by \
reading input from stdin and printing the answer to stdout. Write your own independent \
implementation — do not assume any particular algorithm. Reply with ONLY the Python code, \
no markdown fences, no explanation."""

_MAX_CATALOG_ENTRIES = 200
_TOPIC_PERSIST_CAP = 25  # don't keep stacking generated problems onto an already-deep topic


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


async def _run_solution(source: str, inputs: list[str]) -> list[str] | None:
    """Runs `source` against every input through the real sandbox (never exec()'d
    in this process). Returns observed stdout per input, or None on any crash."""
    outputs = []
    for stdin in inputs:
        result = await piston.run_code("python", "3.10.0", source, stdin=stdin)
        raw = result.get("run", {})
        if raw.get("stderr") and raw.get("code", 0) != 0:
            return None
        outputs.append(raw.get("stdout", ""))
    return outputs


def _ask_llm(system: str, user: str, temperature: float, max_tokens: int) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from services.llm import _make_llm
    llm = _make_llm(temperature=temperature, max_tokens=max_tokens)
    result = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return _strip_fences(str(result.content))


def _ask_llm_fallback(system: str, user: str, temperature: float, max_tokens: int) -> str:
    from services.llm import _fallback_chat
    return _strip_fences(_fallback_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens, temperature,
    ))


async def select_or_generate_question(role: str, candidate_intro: str = "") -> dict | None:
    """Main entry point. Returns a question dict (bank-shaped) or None if even
    the existing-bank fallback has nothing for this track — callers should
    treat None the same as "no canonical problem available".

    candidate_intro: the candidate's actual introduction/background, if available.
    Without real per-candidate context the model has nothing to differentiate
    sessions on and tends to converge on the same "obvious" answer every time —
    pass this whenever you have it (i.e. call this after the candidate's first
    reply, not at session start before they've said anything)."""
    all_questions = await asyncio.to_thread(question_bank._all_questions)
    technical = [q for q in all_questions if q.get("track") == "technical"]
    if not technical:
        return None

    catalog = _build_catalog(technical)
    system = _DECIDE_OR_GENERATE_SYSTEM.format(
        role=role, candidate_intro=candidate_intro or "(not provided)", catalog=catalog,
    )
    user_msg = "Choose now."

    try:
        raw = await asyncio.to_thread(_ask_llm, system, user_msg, 1.0, 1200)
    except Exception:
        try:
            raw = await asyncio.to_thread(_ask_llm_fallback, system, user_msg, 1.0, 1200)
        except Exception:
            return question_bank.pick_question("technical", language="python")

    try:
        spec = json.loads(raw)
        action = spec.get("action")
    except json.JSONDecodeError:
        return question_bank.pick_question("technical", language="python")

    if action == "use_existing":
        found = question_bank.get_question(spec.get("id", ""))
        return found or question_bank.pick_question("technical", language="python")

    if action != "generate":
        return question_bank.pick_question("technical", language="python")

    try:
        title, topic, difficulty = spec["title"], spec["topic"], spec["difficulty"]
        prompt, solution, inputs = spec["prompt"], spec["solution_python"], spec["inputs"]
        claimed = spec.get("claimed_outputs") or []
        if not inputs or len(inputs) < 3:
            return question_bank.pick_question("technical", language="python")
    except (KeyError, TypeError):
        return question_bank.pick_question("technical", language="python")

    actual_outputs = await _run_solution(solution, inputs)
    if actual_outputs is None:
        return question_bank.pick_question("technical", language="python")

    # Cross-check the LLM's own claimed outputs against sandbox ground truth —
    # free (no extra LLM call), catches the LLM narrating a different result
    # than what its own code actually does. The sandbox result is what we keep
    # either way; a high mismatch rate means this problem isn't trustworthy.
    if claimed and len(claimed) == len(actual_outputs):
        mismatches = sum(1 for c, a in zip(claimed, actual_outputs) if _normalize(c) != _normalize(a))
        if mismatches > 1:
            return question_bank.pick_question("technical", language="python")

    tests = [{"stdin": i, "stdout": o} for i, o in zip(inputs, actual_outputs)]
    question = {
        "id": _slugify(title),
        "track": "technical",
        "topic": topic,
        "difficulty": difficulty,
        "title": title,
        "prompt": prompt,
        "function_name": None,
        "languages": ["python"],
        "tests": tests,
        "visible_count": min(3, len(tests)),
    }

    # Persistence is the expensive, durable commitment (every future candidate
    # may get this problem), so it gets the heavier dual-solution bar — but
    # only if it clears a cheap, free uniqueness check first.
    topic_count = sum(1 for q in technical if q.get("topic") == topic)
    title_collision = any(q["title"].strip().lower() == title.strip().lower() for q in technical)
    if not title_collision and topic_count < _TOPIC_PERSIST_CAP:
        asyncio.create_task(_verify_and_persist(question, prompt, inputs, actual_outputs))

    return question


async def _verify_and_persist(question: dict, prompt: str, inputs: list[str], primary_outputs: list[str]) -> None:
    """Runs in the background (doesn't block the candidate's session) — gets a
    second, independently-prompted solution and only writes to Supabase if it
    agrees with the primary solution on every input."""
    try:
        solution_b = await asyncio.to_thread(_ask_llm, _SECOND_SOLUTION_SYSTEM, prompt, 0.5, 800)
    except Exception:
        return

    outputs_b = await _run_solution(solution_b, inputs)
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
        }).execute()
        question_bank.refresh()
    except Exception:
        pass
