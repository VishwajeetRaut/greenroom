"""
Dynamic test runner for technical interviews.

Two-step approach:
  1. Ask the LLM to produce ONLY test-case data (a JSON array) — no code.
  2. We inject that data into a harness template we control, so syntax is always correct.
"""

from __future__ import annotations
import asyncio
import json
import re


# ── Step 1: LLM generates test-case data only ────────────────────────────────

_CASES_SYSTEM = """\
You generate test cases for coding interview problems.
Return ONLY a valid JSON array — no explanation, no markdown, no code fences.

Each element must have exactly two string fields:
  "call"     — Python statements (separated by ";") ending in the expression to check, e.g.
               "two_sum([2,7,11,15], 9)" for a plain function, or for a stateful object:
               "obj = LRUCache(2); obj.put(1, 1); obj.put(2, 2); obj.get(1)"
               Each test case is independent — always declare and assign a fresh object with
               the exact same variable name ("obj") rather than reusing one from another case.
  "expected" — the expected return value as a literal, e.g. "[0, 1]"

Generate exactly 6 test cases: 3 typical inputs first, then 3 edge cases.
Output nothing except the JSON array."""


def _extract_problem(history: list[dict]) -> str:
    """Return interviewer messages after the candidate's first reply — that is where the problem lives."""
    past_intro = False
    parts: list[str] = []
    for turn in history:
        if turn["role"] == "candidate":
            past_intro = True
        if past_intro and turn["role"] == "interviewer":
            parts.append(turn["content"])
    return "\n\n".join(parts[:3])


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _generate_cases(problem: str) -> list[dict] | None:
    from services.llm import _make_llm, _fallback_chat
    from langchain_core.messages import SystemMessage, HumanMessage

    prompt = f"Problem:\n{problem}\n\nReturn the 6 test cases as a JSON array now."
    msgs = [
        {"role": "system", "content": _CASES_SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    try:
        llm = _make_llm(temperature=0.1, max_tokens=600)
        result = llm.invoke([SystemMessage(content=_CASES_SYSTEM), HumanMessage(content=prompt)])
        raw = _strip_fences(result.content)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            raw = _strip_fences(_fallback_chat(msgs, max_tokens=600, temperature=0.1))
        else:
            return None

    try:
        cases = json.loads(raw)
        if isinstance(cases, list) and cases:
            return cases
    except json.JSONDecodeError:
        pass
    return None


# ── Step 2: We write the harness; only the data comes from the LLM ───────────

def _python_harness(source: str, cases: list[dict]) -> str:
    cases_json = json.dumps(cases)
    return f'''{source}

import json as _j
import ast as _ast

_cases = {cases_json}
_visible = _cases[:3]
_hidden  = _cases[3:]

def _eq(a, b):
    if a == b:
        return True
    try:
        return sorted(list(a)) == sorted(list(b))
    except Exception:
        return False

def _run_call(code):
    """Supports multi-statement test cases (e.g. stateful objects: 'o = Foo(); o.put(1); o.get()'),
    not just single expressions — Python's eval() alone only accepts one expression."""
    tree = _ast.parse(code, mode="exec")
    if not tree.body:
        return None
    last = tree.body[-1]
    ns = globals()
    if isinstance(last, _ast.Expr):
        if len(tree.body) > 1:
            exec(compile(_ast.Module(body=tree.body[:-1], type_ignores=[]), "<test>", "exec"), ns)
        return eval(compile(_ast.Expression(body=last.value), "<test>", "eval"), ns)
    exec(compile(tree, "<test>", "exec"), ns)
    return None

for _i, _tc in enumerate(_visible):
    try:
        _result   = _run_call(_tc["call"])
        _expected = eval(_tc["expected"])
        _passed   = _eq(_result, _expected)
        _out = {{"id": _i+1, "label": f"Case {{_i+1}}", "input": _tc["call"], "expected": _tc["expected"], "passed": _passed}}
        if not _passed:
            _out["actual"] = str(_result)
        print(_j.dumps(_out))
    except Exception as _e:
        print(_j.dumps({{"id": _i+1, "label": f"Case {{_i+1}}", "input": _tc["call"],
                         "expected": _tc["expected"], "passed": False, "actual": f"ERROR: {{_e}}"}}))

for _i, _tc in enumerate(_hidden):
    try:
        _result   = _run_call(_tc["call"])
        _expected = eval(_tc["expected"])
        _passed   = _eq(_result, _expected)
        print(_j.dumps({{"id": _i+4, "label": f"Hidden {{_i+1}}", "hidden": True, "passed": _passed}}))
    except Exception:
        print(_j.dumps({{"id": _i+4, "label": f"Hidden {{_i+1}}", "hidden": True, "passed": False}}))
'''


def _node_harness(source: str, cases: list[dict]) -> str:
    cases_json = json.dumps(cases)
    return f'''{source}

const _cases = {cases_json};
const _visible = _cases.slice(0, 3);
const _hidden  = _cases.slice(3);

function _eq(a, b) {{
    if (JSON.stringify(a) === JSON.stringify(b)) return true;
    if (Array.isArray(a) && Array.isArray(b)) {{
        return JSON.stringify([...a].sort((x,y)=>x-y)) === JSON.stringify([...b].sort((x,y)=>x-y));
    }}
    return false;
}}

_visible.forEach((_tc, _i) => {{
    try {{
        const _result   = eval(_tc.call);
        const _expected = eval(_tc.expected);
        const _passed   = _eq(_result, _expected);
        const _out = {{id:_i+1, label:`Case ${{_i+1}}`, input:_tc.call, expected:_tc.expected, passed:_passed}};
        if (!_passed) _out.actual = JSON.stringify(_result);
        console.log(JSON.stringify(_out));
    }} catch(_e) {{
        console.log(JSON.stringify({{id:_i+1, label:`Case ${{_i+1}}`, input:_tc.call,
            expected:_tc.expected, passed:false, actual:`ERROR: ${{_e.message}}`}}));
    }}
}});

_hidden.forEach((_tc, _i) => {{
    try {{
        const _result   = eval(_tc.call);
        const _expected = eval(_tc.expected);
        const _passed   = _eq(_result, _expected);
        console.log(JSON.stringify({{id:_i+4, label:`Hidden ${{_i+1}}`, hidden:true, passed:_passed}}));
    }} catch(_e) {{
        console.log(JSON.stringify({{id:_i+4, label:`Hidden ${{_i+1}}`, hidden:true, passed:false}}));
    }}
}});
'''


def generate_harness(language: str, source: str, history: list[dict], assigned_question: dict | None = None) -> str | None:
    """
    Prefers canonical, pre-verified test cases from the curated question bank
    (assigned_question — see services/question_bank.py) when the session was
    given one of those problems. Only falls back to LLM-generated cases for
    ad hoc problems the interviewer invented on its own.
    """
    if assigned_question and language in (assigned_question.get("languages") or []):
        cases = assigned_question["tests"]
    else:
        problem = _extract_problem(history)
        if not problem:
            return None
        cases = _generate_cases(problem)
        if not cases:
            return None

    if language == "python":
        return _python_harness(source, cases)
    if language == "node":
        return _node_harness(source, cases)
    return None  # Java/C++ not yet supported — caller shows appropriate message


# ── stdin/stdout test mode — language-agnostic, used by the CodeContests-derived
# entries in the question bank (see services/question_bank.py). Unlike the
# call/expected mode above, no harness injection is needed: the candidate's raw
# source IS the program, run once per test case with that case's stdin, and its
# stdout is diffed directly. This is the same protocol Codeforces/Judge0 use,
# and it works identically for Python/JS/Java/C++ with zero per-language code.

def _normalize_output(text: str) -> str:
    return "\n".join(line.rstrip() for line in (text or "").strip().splitlines())


async def run_stdio_tests(language: str, version: str, source: str, tests: list[dict], visible_count: int = 3) -> dict:
    from services import piston

    async def _run_one(tc: dict) -> dict:
        result = await piston.run_code(language, version, source, stdin=tc["stdin"])
        raw = result.get("run", {})
        stdout, stderr = raw.get("stdout", ""), raw.get("stderr", "")
        crashed = bool(stderr) and raw.get("code", 0) != 0
        passed = (not crashed) and _normalize_output(stdout) == _normalize_output(tc["stdout"])
        return {"passed": passed, "stdout": stdout, "stderr": stderr, "crashed": crashed}

    results = await asyncio.gather(*(_run_one(tc) for tc in tests))

    # A real compile/syntax error fails identically on every test case (it never
    # gets to read input at all). A runtime exception triggered by a specific
    # input crashes on some cases but not others. That distinction is the only
    # reliable signal we have, since Piston/Wandbox bundle compile+run per call.
    if results and all(r["crashed"] for r in results):
        err = results[0]["stderr"]
        return {
            "status": "compile_error",
            "compile_error": err[:1500],
            "error_type": _classify_error(err),
            "visible_tests": [], "hidden_tests": [], "passed": 0, "total": 0,
        }

    visible_tests, hidden_tests = [], []
    passed = 0
    any_crash = False
    for i, (tc, r) in enumerate(zip(tests, results)):
        if r["passed"]:
            passed += 1
        if r["crashed"] and not r["passed"]:
            any_crash = True
        if i < visible_count:
            entry = {
                "id": i + 1, "label": f"Case {i + 1}",
                "input": tc["stdin"], "expected": tc["stdout"],
                "output": tc["stdout"] if r["passed"] else r["stdout"],
                "passed": r["passed"],
            }
            if not r["passed"]:
                entry["error"] = (
                    f"Program crashed:\n{r['stderr']}" if r["crashed"]
                    else f"Expected:\n{tc['stdout']}\n\nGot:\n{r['stdout']}"
                )
            visible_tests.append(entry)
        else:
            hidden_tests.append({"id": i + 1, "passed": r["passed"]})

    total = len(tests)
    status = "accepted" if passed == total else ("runtime_error" if any_crash else "wrong_answer")

    return {
        "status": status,
        "visible_tests": visible_tests,
        "hidden_tests": hidden_tests,
        "passed": passed,
        "total": total,
        "error_type": "permanent" if status != "accepted" else None,
    }


# ── Result parser (call/expected mode) ────────────────────────────────────────

_TRANSIENT_MARKERS = (
    "currently unavailable", "timed out", "timeout", "connection",
    "temporarily unavailable", "rate limit", "503", "502", "service unavailable",
)


def _classify_error(text: str) -> str:
    """'transient' — an infra/sandbox problem, safe to retry as-is.
    'permanent' — the candidate's own code raised this; retrying won't help."""
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return "transient"
    return "permanent"


def parse_results(stdout: str, stderr: str) -> dict:
    if stderr and not stdout.strip():
        return {
            "status": "compile_error",
            "compile_error": stderr[:1500],
            "error_type": _classify_error(stderr),
            "visible_tests": [], "hidden_tests": [], "passed": 0, "total": 0,
        }

    visible_tests: list[dict] = []
    hidden_tests:  list[dict] = []
    passed = 0

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tc = json.loads(line)
        except json.JSONDecodeError:
            continue

        tc_passed = bool(tc.get("passed", False))
        if tc_passed:
            passed += 1

        if tc.get("hidden"):
            hidden_tests.append({"id": tc.get("id", len(hidden_tests)), "passed": tc_passed})
        else:
            entry: dict = {
                "id":       tc.get("id", len(visible_tests) + 1),
                "label":    tc.get("label", f"Case {len(visible_tests) + 1}"),
                "input":    tc.get("input", ""),
                "expected": tc.get("expected", ""),
                "output":   tc.get("actual") if not tc_passed else tc.get("expected", ""),
                "passed":   tc_passed,
            }
            if "actual" in tc and not tc_passed and "ERROR" in str(tc.get("actual", "")):
                entry["error"] = tc["actual"]
            visible_tests.append(entry)

    total = len(visible_tests) + len(hidden_tests)

    if total == 0:
        msg = stderr[:1500] if stderr else "No test output produced. Check your code for syntax errors."
        return {
            "status": "compile_error",
            "compile_error": msg,
            "error_type": _classify_error(stderr) if stderr else "permanent",
            "visible_tests": [], "hidden_tests": [], "passed": 0, "total": 0,
        }

    errors = [r.get("error") for r in visible_tests if r.get("error")]
    any_error = bool(errors)
    status = "accepted" if passed == total else ("runtime_error" if any_error else "wrong_answer")

    return {
        "status": status,
        "visible_tests": visible_tests,
        "hidden_tests":  hidden_tests,
        "passed":        passed,
        "total":         total,
        "error_type":    _classify_error(errors[0]) if errors else None,
    }
