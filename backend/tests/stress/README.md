# Stress tests

Concurrency and load tests for the components that break first under load:
the dynamic interviewer, the evaluation engine, the guardrails engine, and the
process-wide stores they share (`job_store`, `session_store`, `rate_limit`).

## Running

They are part of the normal suite and take well under a second:

```bash
pytest tests/stress/          # just these
pytest tests/                 # everything, stress included
pytest tests/ -m "not stress" # skip them
```

No credentials or network access are needed.

## How these are written

**Every LLM seam is stubbed.** The interviewer, the evaluation engine and the
guardrail judge all call Groq. Driving them at load against the real endpoint
would burn credits and trip provider rate limits, so `conftest.py` swaps the
model for a scripted fake.

The `assert_no_network` fixture is autouse and fails any test that lets an HTTP
call escape. This matters more than it looks: `guardrail._llm_judge`,
`llm._self_critique` and `llm.evaluate_session` all catch broad exceptions and
fail open, so an accidental real call would be silently swallowed and the test
would still pass while quietly hitting the network.

**Assertions are on invariants, not wall-clock.** Timing assertions on a shared
CI runner are flaky. These tests assert properties that hold regardless of how
the scheduler interleaves: job ids are unique, no result write is lost, one lock
per session, the limiter admits exactly its limit. The one deliberate exception
is `test_regex_scales_linearly`, a ReDoS tripwire with a loose bound.

**Stub the boundary, not the guard.** When a component already swallows its own
failures, replacing the whole function tests nothing — the swallow is the thing
under test. `test_guardrail_judge_fails_open_when_network_dies` restores the
real `_llm_judge` and severs its network call instead.

## Known bugs pinned here

Tests are `xfail(strict=True)` against real bugs found while writing this suite.
Strict means they fail if the bug is fixed and the marker is left behind, so the
fix and the marker removal land together.

| Test | Bug | Status |
|---|---|---|
| `test_postgres_limiter_enforces_limit_under_burst` | `_check_postgres` counted then inserted non-atomically, so a burst was admitted whole — no rate limiting under the exact condition it exists for. | fixed; now a regression test |
| `test_evaluation_survives_dead_primary_without_fallback` | `_fallback_chat`'s `RuntimeError` escapes `evaluate_session` when no fallback is configured, so a Groq 429 becomes a 500 instead of the last-resort report. | still xfail |
