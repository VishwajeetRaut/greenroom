#!/usr/bin/env python3
"""
Benchmark candidate models against Greenroom's ACTUAL workloads.

Run:
    cd backend
    python scripts/benchmark_models.py                      # all models, 3 reps
    python scripts/benchmark_models.py --repeat 5
    python scripts/benchmark_models.py --models llama-3.1-8b-instant,openai/gpt-oss-20b
    python scripts/benchmark_models.py --out ../docs/model_benchmark_raw.json

Why these workloads
-------------------
A generic benchmark (MMLU, latency-per-token) says nothing about whether a
model can run *this* interview. Each workload below uses the real production
prompt constant and the real max_tokens, so the numbers transfer directly:

    opening      OPENING_SYSTEM_PROMPT     — short creative generation, once/session
    turn         TRACK_PERSONAS[technical] — the per-turn workhorse; runs N times/session
    evaluate     EVAL_SYSTEM_PROMPT        — structured JSON over a full transcript
    testcases    _CASES_SYSTEM             — JSON array generation
    judge        guardrail YES/NO prompt   — binary classification, fires every turn

Each workload carries its own pass/fail check, because a model that is 10x
cheaper but returns unparseable JSON for `evaluate` is not cheaper — it's
unusable. Cost without a correctness signal is a misleading number, so the
two are always reported together.

Every model sees byte-identical inputs (the transcript and history below are
fixed constants), so differences in reported input tokens reflect genuine
tokenizer differences, not different prompts.

Output
------
A markdown table on stdout plus, with --out, the raw per-run JSON. Token
counts are provider-reported, not estimated. Costs use
services.token_meter.PRICING — see the caveat on that constant.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402

from services import guardrail, token_meter  # noqa: E402
from services.llm import (  # noqa: E402
    EVAL_SYSTEM_PROMPT,
    OPENING_SYSTEM_PROMPT,
    TRACK_PERSONAS,
    _make_llm,
)
from services.test_runner import _CASES_SYSTEM  # noqa: E402

DEFAULT_MODELS = [
    "llama-3.3-70b-versatile",   # current production model
    "llama-3.1-8b-instant",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
]

ROLE = "backend engineer"

# ── fixed inputs, identical for every model ──────────────────────────────────

HISTORY = [
    ("interviewer", "Hi, thanks for joining. Tell me a bit about your background."),
    ("candidate", "I'm a backend engineer with about three years of experience, mostly Python "
                  "and FastAPI services on AWS. Recently I've been working on a payments "
                  "reconciliation pipeline that processes a few million rows a day."),
    ("interviewer", "Nice. Let's do a coding problem: given an array of integers and a target, "
                    "return the indices of the two numbers that add up to the target."),
    ("candidate", "I'd use a hash map. As I iterate I store each value's index, and for each "
                  "element I check whether target minus that element is already in the map. "
                  "Here's my implementation:\n\ndef two_sum(nums, target):\n"
                  "    seen = {}\n    for i, n in enumerate(nums):\n"
                  "        if target - n in seen:\n            return [seen[target - n], i]\n"
                  "        seen[n] = i\n    return []"),
]

TRANSCRIPT = "\n".join(
    f"{'Interviewer' if role == 'interviewer' else 'Candidate'}: {content}"
    for role, content in HISTORY
)

CODING_PROBLEM = (
    "Given an array of integers nums and an integer target, return the indices of the two "
    "numbers such that they add up to target. You may assume exactly one valid answer exists."
)

# A draft that genuinely leaks complexity, and one that doesn't — the judge
# has to get BOTH right, otherwise "accuracy" would be satisfiable by a model
# that just always answers NO.
LEAKY_DRAFT = "Good — that's the optimal approach, it runs in O(n) time and O(n) space. Any edge cases?"
CLEAN_DRAFT = "Good. What's the time complexity of that approach, and why?"


# ── workloads ────────────────────────────────────────────────────────────────

# Multiplier applied to every workload's production max_tokens (--token-scale).
#
# This exists because several candidate models are REASONING models: qwen3.6
# emits a <think> trace into the content channel, and the gpt-oss models spend
# a hidden reasoning budget, both of which count against max_tokens. At
# production budgets (judge=3, testcases=600) the visible answer never
# arrives, which looks like a capability failure but is really a budget one.
# Re-running with a raised scale separates "this model can't do the task" from
# "this model wasn't given room to answer" — the two have completely different
# consequences for whether it's adoptable.
TOKEN_SCALE = 1.0


def _invoke(model: str, messages: list, max_tokens: int, temperature: float, json_mode: bool = False):
    llm = _make_llm(temperature=temperature, max_tokens=int(max_tokens * TOKEN_SCALE),
                    call_site="benchmark", model=model)
    if json_mode:
        llm = llm.bind(response_format={"type": "json_object"})
    return llm.invoke(messages)


def wl_opening(model: str) -> tuple[str, bool, str]:
    system = OPENING_SYSTEM_PROMPT.format(track="technical", role=ROLE)
    out = _invoke(model, [
        SystemMessage(content=system),
        HumanMessage(content="[The interview session is starting now.]"),
    ], max_tokens=120, temperature=0.9).content.strip()

    # Production requires: non-empty, brief, and never admitting it's an AI.
    if not out:
        return out, False, "empty response"
    if "as an ai" in out.lower() or "language model" in out.lower():
        return out, False, "broke character"
    if len(out) > 700:
        return out, False, f"too long ({len(out)} chars, prompt says 2-3 sentences)"
    return out, True, ""


def wl_turn(model: str) -> tuple[str, bool, str]:
    system = TRACK_PERSONAS["technical"].format(role=ROLE) + (
        f"\n\nThe coding problem assigned to this candidate is exactly this one: {CODING_PROBLEM}"
    )
    messages = [SystemMessage(content=system)]
    for role, content in HISTORY[:-1]:
        messages.append(AIMessage(content=content) if role == "interviewer" else HumanMessage(content=content))
    messages.append(HumanMessage(content=HISTORY[-1][1]))

    out = _invoke(model, messages, max_tokens=200, temperature=0.7).content.strip()
    if not out:
        return out, False, "empty response"
    # The single hardest production constraint on this call: never state the
    # complexity. This is exactly what services.guardrail exists to catch, so
    # the benchmark reuses that same detector rather than a bespoke one.
    if guardrail.violates(out, "technical"):
        return out, False, "leaked complexity (guardrail regex)"
    return out, True, ""


def wl_evaluate(model: str) -> tuple[str, bool, str]:
    system = EVAL_SYSTEM_PROMPT.format(track="technical", role=ROLE)
    out = _invoke(model, [
        SystemMessage(content=system),
        HumanMessage(content=TRANSCRIPT),
    ], max_tokens=700, temperature=0.3, json_mode=True).content.strip()

    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        return out, False, f"invalid JSON: {exc}"
    missing = [k for k in ("overall_score", "summary", "star_analysis", "evaluations") if k not in parsed]
    if missing:
        return out, False, f"missing keys: {', '.join(missing)}"
    if not isinstance(parsed.get("evaluations"), list) or not parsed["evaluations"]:
        return out, False, "evaluations empty"
    return out, True, ""


def wl_testcases(model: str) -> tuple[str, bool, str]:
    prompt = f"Problem:\n{CODING_PROBLEM}\n\nReturn the 6 test cases as a JSON array now."
    out = _invoke(model, [
        SystemMessage(content=_CASES_SYSTEM),
        HumanMessage(content=prompt),
    ], max_tokens=600, temperature=0.1).content.strip()

    cleaned = out
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        cases = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return out, False, f"invalid JSON: {exc}"
    if not isinstance(cases, list) or not cases:
        return out, False, "not a non-empty array"
    if not all(isinstance(c, dict) and "call" in c and "expected" in c for c in cases):
        return out, False, "entries missing call/expected"
    return out, True, ""


def wl_judge(model: str) -> tuple[str, bool, str]:
    """Both directions, so always-NO can't score as accurate."""
    template = (
        "Does the following interviewer message reveal the time or space complexity "
        "of any solution (e.g. O(n), O(1), linear time, constant space), or state "
        "that a solution is optimal without asking the candidate to verify? "
        "Reply YES or NO only.\n\n"
    )
    leaky = _invoke(model, [HumanMessage(content=template + LEAKY_DRAFT)],
                    max_tokens=3, temperature=0).content.strip().upper()
    clean = _invoke(model, [HumanMessage(content=template + CLEAN_DRAFT)],
                    max_tokens=3, temperature=0).content.strip().upper()
    out = f"leaky->{leaky!r} clean->{clean!r}"
    if not leaky.startswith("YES"):
        return out, False, "missed a real leak (false negative)"
    if clean.startswith("YES"):
        return out, False, "flagged a clean question (false positive)"
    return out, True, ""


WORKLOADS = {
    "opening":   (wl_opening,   "Session greeting (1x per session)"),
    "turn":      (wl_turn,      "Interviewer turn (N x per session)"),
    "evaluate":  (wl_evaluate,  "Final evaluation, JSON (1x per session)"),
    "testcases": (wl_testcases, "Test-case generation, JSON (1x per problem)"),
    "judge":     (wl_judge,     "Guardrail YES/NO (1x per turn)"),
}


# ── runner ───────────────────────────────────────────────────────────────────

def run_once(model: str, name: str) -> dict:
    fn, _ = WORKLOADS[name]
    token_meter.clear()
    start = time.perf_counter()
    try:
        output, ok, reason = fn(model)
        error = None
    except Exception as exc:  # model rejected the request, rate limit, timeout...
        output, ok, reason, error = "", False, type(exc).__name__, str(exc)[:300]
    latency_ms = round((time.perf_counter() - start) * 1000)

    snap = token_meter.stats()
    return {
        "model": model,
        "workload": name,
        "ok": ok,
        "reason": reason,
        "error": error,
        "latency_ms": latency_ms,
        "input_tokens": snap["total_input_tokens"],
        "output_tokens": snap["total_output_tokens"],
        "cost_usd": snap["total_cost_usd"],
        "sample": output[:200],
    }


def summarize(runs: list[dict]) -> list[dict]:
    rows = []
    for model in dict.fromkeys(r["model"] for r in runs):
        for name in WORKLOADS:
            group = [r for r in runs if r["model"] == model and r["workload"] == name]
            if not group:
                continue
            ok = [r for r in group if r["ok"]]
            # Latency/tokens are averaged over SUCCESSFUL runs only — a run
            # that failed in 200ms would otherwise flatter a broken model.
            basis = ok or group
            rows.append({
                "model": model,
                "workload": name,
                "runs": len(group),
                "pass_rate": round(len(ok) / len(group), 2),
                "p50_latency_ms": round(statistics.median(r["latency_ms"] for r in basis)),
                "avg_input_tokens": round(statistics.mean(r["input_tokens"] for r in basis)),
                "avg_output_tokens": round(statistics.mean(r["output_tokens"] for r in basis)),
                "avg_cost_usd": statistics.mean(r["cost_usd"] for r in basis),
                "failures": sorted({r["reason"] for r in group if not r["ok"]}),
            })
    return rows


# Calls per completed session, used to turn per-call cost into per-session
# cost. Derived from the real flow: one greeting, MAX_CANDIDATE_TURNS turns
# (each turn = one interviewer call + one guardrail judge), one test-case
# generation, one evaluation. The self-critique pass adds a second evaluate
# call when EVAL_SELF_CRITIQUE_ENABLED is on.
TURNS_PER_SESSION = int(os.environ.get("MAX_CANDIDATE_TURNS", "15"))
CALLS_PER_SESSION = {
    "opening": 1,
    "turn": TURNS_PER_SESSION,
    "judge": TURNS_PER_SESSION,
    "testcases": 1,
    "evaluate": 2 if os.environ.get("EVAL_SELF_CRITIQUE_ENABLED", "true").lower() == "true" else 1,
}


def print_report(rows: list[dict]) -> None:
    print("\n## Per-workload results\n")
    print("| Model | Workload | Pass | p50 latency | In tok | Out tok | $/call | Failures |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| `{r['model']}` | {r['workload']} | {r['pass_rate']:.0%} | {r['p50_latency_ms']} ms | "
            f"{r['avg_input_tokens']} | {r['avg_output_tokens']} | ${r['avg_cost_usd']:.6f} | "
            f"{', '.join(r['failures']) or '—'} |"
        )

    print("\n## Projected cost per completed session\n")
    print(f"Assumes {TURNS_PER_SESSION} candidate turns; "
          f"{CALLS_PER_SESSION['evaluate']} evaluate call(s); guardrail judge on every turn.\n")
    print("| Model | $/session | All workloads pass? | Failing workloads |")
    print("|---|---|---|---|")
    for model in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == model]
        total = sum(r["avg_cost_usd"] * CALLS_PER_SESSION.get(r["workload"], 1) for r in mine)
        bad = [r["workload"] for r in mine if r["pass_rate"] < 1.0]
        print(f"| `{model}` | ${total:.4f} | {'yes' if not bad else 'NO'} | {', '.join(bad) or '—'} |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--workloads", default=",".join(WORKLOADS))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between calls (free-tier rate limits)")
    ap.add_argument("--token-scale", type=float, default=1.0,
                    help="multiply every workload's production max_tokens (see TOKEN_SCALE) — "
                         "use >1 to give reasoning models room for their think trace")
    ap.add_argument("--out", help="write raw per-run JSON here")
    args = ap.parse_args()

    global TOKEN_SCALE
    TOKEN_SCALE = args.token_scale

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    names = [w.strip() for w in args.workloads.split(",") if w.strip() in WORKLOADS]

    runs: list[dict] = []
    total = len(models) * len(names) * args.repeat
    done = 0
    for model in models:
        for name in names:
            for _ in range(args.repeat):
                result = run_once(model, name)
                runs.append(result)
                done += 1
                status = "ok " if result["ok"] else "FAIL"
                print(f"[{done}/{total}] {status} {model} {name} "
                      f"{result['latency_ms']}ms in={result['input_tokens']} out={result['output_tokens']}"
                      + (f"  ({result['reason']})" if not result["ok"] else ""),
                      file=sys.stderr, flush=True)
                time.sleep(args.sleep)

    rows = summarize(runs)
    print_report(rows)

    if args.out:
        Path(args.out).write_text(json.dumps({"runs": runs, "summary": rows}, indent=2))
        print(f"\nRaw results written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
