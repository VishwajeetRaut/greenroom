#!/usr/bin/env python3
"""
Load-test Greenroom and report where it bends.

    cd backend
    python -m uvicorn main:app --port 8000 --no-access-log &

    python scripts/load_test.py --profile baseline  --users 50 --duration 20
    python scripts/load_test.py --profile auth      --users 50 --duration 20
    python scripts/load_test.py --profile mixed     --users 20 --duration 30 --token "$JWT"

Profiles
--------
baseline  /api/health and /metrics — no auth, no LLM. Measures the server's
          own ceiling: how much the event loop and the threadpool can absorb
          before latency runs away.
auth      An authenticated endpoint with a token. Every authenticated request
          makes a network call to Supabase to validate the JWT plus up to
          three more for the rate limiter, so this profile isolates the
          per-request round-trip cost that has nothing to do with the
          interview itself.
mixed     A realistic session shape. Needs --token and BURNS LLM QUOTA — the
          Groq free tier is 100,000 tokens/day and one interview turn is
          ~600. Do not point this at production credentials.

Why not locust/k6
-----------------
Neither is a dependency here, and a load test that can't be run without
installing something is a load test nobody runs before a demo. This is
asyncio + httpx, both already required.

Reading the output
------------------
p99 matters more than the mean. A mean that looks fine while p99 is 10x it
means a minority of candidates are having a broken interview, which is exactly
the failure this is meant to find. The script also diffs the /metrics endpoint
across the run, so server-side counters (LLM fallbacks, sandbox backend,
guardrail triggers) line up with client-side latency.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROFILES = {
    "baseline": [("GET", "/api/health", None), ("GET", "/metrics", None)],
    "auth": [("GET", "/api/interview/does-not-exist/resume", None)],
    "mixed": [("POST", "/api/interview/start", {"track": "behavioral", "role": "Software Engineer"})],
}


class Results:
    def __init__(self) -> None:
        self.latencies: list[float] = []
        self.statuses: Counter = Counter()
        self.errors: Counter = Counter()

    def record(self, status: int | None, seconds: float, error: str | None = None) -> None:
        self.latencies.append(seconds)
        if error:
            self.errors[error] += 1
        else:
            self.statuses[status] += 1

    def summary(self, duration: float) -> dict:
        if not self.latencies:
            return {"requests": 0}
        ordered = sorted(self.latencies)

        def pct(p: float) -> float:
            return ordered[min(int(len(ordered) * p), len(ordered) - 1)]

        total = len(ordered)
        bad = sum(count for status, count in self.statuses.items() if status and status >= 500)
        return {
            "requests": total,
            "rps": round(total / duration, 1),
            "mean_ms": round(statistics.fmean(ordered) * 1000, 1),
            "p50_ms": round(pct(0.50) * 1000, 1),
            "p95_ms": round(pct(0.95) * 1000, 1),
            "p99_ms": round(pct(0.99) * 1000, 1),
            "max_ms": round(ordered[-1] * 1000, 1),
            "statuses": dict(self.statuses),
            "errors": dict(self.errors),
            "server_error_rate": round(bad / total, 4),
        }


async def _worker(client: httpx.AsyncClient, base: str, requests, deadline: float,
                  results: Results, headers: dict) -> None:
    index = 0
    while time.monotonic() < deadline:
        method, path, body = requests[index % len(requests)]
        index += 1
        start = time.monotonic()
        try:
            response = await client.request(method, base + path, json=body, headers=headers, timeout=30)
            results.record(response.status_code, time.monotonic() - start)
        except Exception as exc:
            results.record(None, time.monotonic() - start, error=type(exc).__name__)


async def _scrape(base: str) -> dict[str, float]:
    """Server-side counters, so client latency can be read against what the
    server thought it was doing."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            body = (await client.get(base + "/metrics")).text
    except Exception:
        return {}
    out: dict[str, float] = {}
    for line in body.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        name, _, value = line.rpartition(" ")
        try:
            out[name] = float(value)
        except ValueError:
            pass
    return out


def _counter_delta(before: dict, after: dict, prefix: str) -> dict:
    out = {}
    for key, value in after.items():
        if key.startswith(prefix):
            change = value - before.get(key, 0.0)
            if change:
                out[key] = round(change, 4)
    return out


async def run(args) -> int:
    requests = PROFILES[args.profile]
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    results = Results()

    print(f"profile={args.profile} users={args.users} duration={args.duration}s target={args.base}")
    if args.profile == "mixed" and not args.token:
        print("ERROR: --profile mixed needs --token", file=sys.stderr)
        return 2

    before = await _scrape(args.base)
    started = time.monotonic()
    deadline = started + args.duration

    limits = httpx.Limits(max_connections=args.users * 2, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(*[
            _worker(client, args.base, requests, deadline, results, headers)
            for _ in range(args.users)
        ])
    elapsed = time.monotonic() - started
    after = await _scrape(args.base)

    summary = results.summary(elapsed)
    print("\n── client ──")
    for key in ("requests", "rps", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"):
        print(f"  {key:18} {summary.get(key)}")
    print(f"  {'statuses':18} {summary.get('statuses')}")
    if summary.get("errors"):
        print(f"  {'errors':18} {summary['errors']}")
    print(f"  {'server_error_rate':18} {summary.get('server_error_rate')}")

    interesting = {
        "LLM calls": "greenroom_llm_calls_total",
        "LLM fallbacks": "greenroom_llm_fallback_total",
        "Sandbox runs": "greenroom_sandbox_runs_total",
        "Guardrail": "greenroom_guardrail_checks_total",
        "Evaluations": "greenroom_evaluations_total",
    }
    deltas = {label: _counter_delta(before, after, prefix)
              for label, prefix in interesting.items()}
    if any(deltas.values()):
        print("\n── server counters (delta over the run) ──")
        for label, values in deltas.items():
            for key, change in values.items():
                print(f"  {label:14} {key.split('{')[0]:38} +{change}")

    # A tail this long means some candidates are having a broken interview even
    # though the mean looks fine — the number to act on.
    if summary.get("p99_ms") and summary.get("p50_ms"):
        ratio = summary["p99_ms"] / max(summary["p50_ms"], 0.01)
        print(f"\n  p99/p50 tail ratio: {ratio:.1f}x"
              + ("   <- long tail, investigate" if ratio > 5 else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="baseline")
    ap.add_argument("--users", type=int, default=25, help="concurrent workers")
    ap.add_argument("--duration", type=int, default=15, help="seconds")
    ap.add_argument("--token", help="Bearer JWT (required for auth/mixed profiles)")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
