"""
On-demand Java/C++ test harnesses for LeetCode-style ("call"/"expected")
question-bank entries, which were originally Python/JS-only (the imported
LeetCodeDataset test cases are Python literals).

Generated lazily — the first time a candidate picks a language a problem
doesn't have yet — rather than batch-generated for all 218 problems upfront:
most (problem, language) pairs will never actually be requested, so paying
generation+verification cost only for the ones that are is the same
credit-conscious principle used by services.question_generator.

Never trusted on the LLM's say-so: the generated "solution" is compiled and
run through the SAME sandbox candidates' own code runs through
(services.piston — Wandbox/Piston, not a special-cased verification path),
and a harness is only cached/served if every test case it reports comes back
passed:true. A failed verification just means that (question, language) pair
isn't offered — the candidate falls back to whatever languages the problem
already supports, same as before this feature existed.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from services import piston

_BOUNDARY = "###---{}---###"

_SYSTEM = """\
You generate a {language} test harness for a coding interview problem, given its Python \
reference data. You will be shown the problem prompt and its existing Python test cases \
(call/expected pairs, already verified correct).

Reply with exactly three sections, each starting with its own marker line (shown below) on a \
line by itself, followed by raw {language} source code — no markdown fences, no JSON, no \
explanation anywhere in your reply.

{boilerplate_marker}
Idiomatic {language} starter code for the candidate's editor — just the class/method signature \
with an empty/TODO body, following standard LeetCode conventions for this language (e.g. Java: \
`class Solution {{ public int[] twoSum(int[] nums, int target) {{ }} }}`).

{solution_marker}
A COMPLETE, correct {language} implementation that actually solves the problem (same public \
signature as the boilerplate, body filled in). This is only used to verify your own harness — \
it is never shown to the candidate.

{harness_marker}
Complete {language} driver code, written so that the CANDIDATE'S code (which has the exact same \
class/method signature as the boilerplate) gets concatenated immediately BEFORE this code in the \
same file. Declare each test's inputs as native {language} literals (translate the given Python \
call/expected literals yourself — arrays, nested lists, booleans, null/None, strings), call the \
candidate's method, and print ONE line of JSON per test case to stdout, in EXACTLY this schema:
  visible tests (the first 3): {{"id": N, "label": "Case N", "input": "<the call as a readable \
string, e.g. twoSum([2,7,11,15], 9)>", "expected": "<expected result as a readable string>", \
"passed": true|false}}
  hidden tests (the rest):     {{"id": N, "label": "Hidden N", "hidden": true, "passed": true|false}}
"input" and "expected" are ONLY required for visible tests — omit them for hidden tests, the \
candidate should not be able to see hidden test data. (This one line of output IS allowed to be \
JSON — it's printed at runtime, not part of your reply.)
Compare results with a deep/structural equality check (e.g. Arrays.equals / Arrays.deepEquals \
for Java arrays, or element-wise vector comparison for C++) — do not rely on reference/pointer \
equality. Wrap each test in its own try/catch so one crashing case doesn't stop the rest; on \
exception, print passed:false for that case. For stateful problems (a constructor plus methods \
to call in sequence, e.g. "obj = LRUCache(2); obj.put(1,1); obj.get(1)"), instantiate a fresh \
object per test case and call the methods in the given order, checking the value of the FINAL \
call against "expected".

For Java: do not declare a `public class` — Wandbox compiles every submission as "prog.java" \
regardless of class name, so only non-public top-level classes/the harness's own top-level code \
may exist. Use a single non-public `class Main` with `public static void main` as your harness \
entry point, with the candidate's `Solution` class (which is also non-public) appended above it.
For C++: include <bits/stdc++.h>, use namespace std, and write a `int main()` harness entry point."""

_VERSION = {"java": "15.0.2", "cpp": "10.2.0"}
_PISTON_LANG = {"java": "java", "cpp": "gcc"}


def merge_java_sources(*pieces: str) -> str:
    """Java only allows `import` statements before any class/interface
    declaration in the whole file. The candidate's code and the generated
    harness are each independently-written snippets that may both have their
    own import block — naively concatenating them puts the harness's imports
    after the candidate's class, which is a compile error. Hoist every import
    to the top instead."""
    imports: list[str] = []
    seen: set[str] = set()
    bodies: list[str] = []
    for piece in pieces:
        body_lines = []
        for line in piece.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") and stripped.endswith(";"):
                if stripped not in seen:
                    seen.add(stripped)
                    imports.append(stripped)
            else:
                body_lines.append(line)
        bodies.append("\n".join(body_lines))
    return "\n".join(imports) + "\n\n" + "\n\n".join(bodies)


def _section(text: str, marker: str, next_markers: list[str]) -> str | None:
    start = text.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = len(text)
    for nm in next_markers:
        idx = text.find(nm, start)
        if idx != -1:
            end = min(end, idx)
    return text[start:end].strip().strip("`").strip()


def _generate(language: str, question: dict) -> dict | None:
    lang_label = "Java" if language == "java" else "C++"
    b_marker, s_marker, h_marker = (_BOUNDARY.format(x) for x in ("BOILERPLATE", "SOLUTION", "HARNESS"))
    system = _SYSTEM.format(
        language=lang_label, boilerplate_marker=b_marker, solution_marker=s_marker, harness_marker=h_marker,
    )
    tests_preview = "\n".join(f'  {t["call"]}  ->  {t["expected"]}' for t in question["tests"])
    user = (
        f"Problem: {question['title']}\n\n{question['prompt']}\n\n"
        f"function_name: {question['function_name']}\n\nTest cases:\n{tests_preview}"
    )
    try:
        from services.llm import _make_llm
        from langchain_core.messages import SystemMessage, HumanMessage
        llm = _make_llm(temperature=0.2, max_tokens=3000)
        result = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        raw = result.content
    except Exception:
        try:
            resp = httpx.post(
                f"{os.environ['FALLBACK_BASE_URL']}/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['FALLBACK_API_KEY']}", "Content-Type": "application/json"},
                json={
                    "model": os.environ["FALLBACK_MODEL"],
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "temperature": 0.2, "max_tokens": 3000,
                },
                timeout=120, follow_redirects=True,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
        except Exception:
            return None

    boilerplate = _section(raw, b_marker, [s_marker, h_marker])
    solution = _section(raw, s_marker, [h_marker])
    harness = _section(raw, h_marker, [])
    if boilerplate and solution and harness:
        return {"boilerplate": boilerplate, "solution": solution, "harness": harness}
    return None


def _parse_result_lines(stdout: str, n_expected: int) -> list[dict] | None:
    results = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if len(results) != n_expected:
        return None
    return results


async def _verify(language: str, spec: dict, n_tests: int) -> bool:
    if language == "java":
        full_source = merge_java_sources(spec["solution"], spec["harness"])
    else:
        full_source = spec["solution"] + "\n\n" + spec["harness"]
    result = await piston.run_code(_PISTON_LANG[language], _VERSION[language], full_source, stdin="")
    raw = result.get("run", {})
    if raw.get("stderr") and raw.get("code", 0) != 0:
        return False
    results = _parse_result_lines(raw.get("stdout", ""), n_tests)
    if results is None:
        return False
    return all(r.get("passed") for r in results)


async def get_or_generate(question: dict, language: str) -> dict | None:
    """Returns {"boilerplate": ..., "harness": ...} for this (question, language)
    pair, generating and sandbox-verifying it on first use and persisting the
    result to Supabase for every future session. Returns None if generation or
    verification fails — callers should treat that exactly like "this language
    isn't supported for this problem" (which, from the candidate's perspective,
    it isn't, at least not yet)."""
    if language not in ("java", "cpp"):
        return None

    cached = (question.get("harnesses") or {}).get(language)
    if cached:
        return cached

    import asyncio
    spec = await asyncio.to_thread(_generate, language, question)
    if not spec:
        return None

    ok = await _verify(language, spec, len(question["tests"]))
    if not ok:
        return None

    harness_data = {"boilerplate": spec["boilerplate"], "harness": spec["harness"]}
    await asyncio.to_thread(_persist, question["id"], language, harness_data)
    return harness_data


def _persist(question_id: str, language: str, harness_data: dict) -> None:
    from services import question_bank
    from services.supabase_client import get_supabase
    sb = get_supabase()
    if not sb:
        return
    try:
        row = sb.table("questions").select("harnesses").eq("id", question_id).execute()
        existing = (row.data[0].get("harnesses") if row.data else None) or {}
        existing[language] = harness_data
        sb.table("questions").update({"harnesses": existing}).eq("id", question_id).execute()
        question_bank.refresh()
    except Exception:
        pass
