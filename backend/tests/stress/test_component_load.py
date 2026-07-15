"""Component-level load tests: dynamic interviewer, evaluation engine, guardrails.

Each component is driven concurrently across all three tracks with its LLM seam
stubbed (see conftest). What's under test is the code around the model call —
prompt assembly, guardrail sanitisation, score reconciliation, fallback handling
— which is where load-related breakage actually lives. The model's own output
quality is not in scope here.
"""

from __future__ import annotations

import time

import httpx
import pytest

import services.guardrail as guardrail_mod
import services.llm as llm_mod

from .conftest import EVAL_JSON, TRACKS, run_concurrent

pytestmark = pytest.mark.stress

CONCURRENCY = 24

# Captured at import, before the autouse fixtures stub them out, so a test can
# put the real implementation back when it wants to exercise the genuine path.
REAL_LLM_JUDGE = guardrail_mod._llm_judge
REAL_SELF_CRITIQUE = llm_mod._self_critique


# ── Dynamic interviewer ───────────────────────────────────────────────────────

@pytest.mark.parametrize("track", TRACKS)
def test_interviewer_concurrent_across_tracks(track, fake_llm):
    """next_question stays correct when many candidates are mid-interview at once."""
    fake_llm(["Walk me through your reasoning on that."])
    history = [{"role": "candidate", "content": "I'd start with a hash map."}]

    results, errors = run_concurrent(
        lambda i: llm_mod.next_question(track, "Software Engineer", history),
        n=CONCURRENCY,
    )

    assert not errors, f"{len(errors)} of {CONCURRENCY} failed; first: {errors[:1]}"
    assert len(results) == CONCURRENCY
    assert all(isinstance(r, str) and r.strip() for r in results)


def test_interviewer_concurrent_mixed_tracks(fake_llm):
    """Tracks interleaved on one process must not bleed prompts into each other.

    Each track has its own persona; a shared-state bug would show up as a track
    getting a response built from another track's system prompt.
    """
    fake_llm(["What trade-offs did you weigh?"])
    history = [{"role": "candidate", "content": "I'd shard by user id."}]

    def ask(i):
        track = TRACKS[i % len(TRACKS)]
        return track, llm_mod.next_question(track, "Software Engineer", history)

    results, errors = run_concurrent(ask, n=CONCURRENCY * 2)

    assert not errors, f"mixed-track load raised: {errors[:1]}"
    assert {t for t, _ in results} == set(TRACKS), "not every track was exercised"
    assert all(isinstance(r, str) and r.strip() for _, r in results)


# ── Evaluation engine ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("track", TRACKS)
def test_evaluation_concurrent_across_tracks(track, fake_llm):
    """Concurrent end-of-session evaluations each return a well-formed report.

    Sessions all end around the same time in a cohort, so this is the realistic
    burst shape for the evaluation engine.
    """
    fake_llm([EVAL_JSON])
    history = [
        {"role": "interviewer", "content": "Tell me about a hard problem."},
        {"role": "candidate", "content": "I led a migration off a legacy queue."},
    ]

    results, errors = run_concurrent(
        lambda i: llm_mod.evaluate_session(track, "Software Engineer", history),
        n=CONCURRENCY,
    )

    assert not errors, f"{len(errors)} of {CONCURRENCY} failed; first: {errors[:1]}"
    for r in results:
        assert "Could not generate" not in r["summary"], "fell back to the last-resort default"
        assert 0 <= r["overall_score"] <= 10
        assert r["evaluations"], "evaluation categories missing"


def test_reconciled_score_is_stable_under_load(fake_llm):
    """_reconcile_score must overwrite the model's self-reported score every time.

    The stub claims overall_score 3 while its categories average 7. Any result
    still showing 3 means reconciliation was skipped or raced.
    """
    fake_llm([EVAL_JSON])
    history = [{"role": "candidate", "content": "I shipped it in two weeks."}]

    results, errors = run_concurrent(
        lambda i: llm_mod.evaluate_session("behavioral", "Software Engineer", history),
        n=CONCURRENCY,
    )

    assert not errors
    scores = {r["overall_score"] for r in results}
    assert scores == {7}, f"expected every score reconciled to 7, saw {scores}"


def test_eval_self_critique_under_load(fake_llm, monkeypatch):
    """Both evaluation passes running concurrently must still yield valid reports.

    Restores the real _self_critique (conftest stubs it out) so the full
    two-call path is what's under load.
    """
    monkeypatch.setattr(llm_mod, "_self_critique", REAL_SELF_CRITIQUE)
    fake_llm([EVAL_JSON])  # cycles, so both passes get valid JSON

    history = [{"role": "candidate", "content": "I reduced p99 latency by 40%."}]
    results, errors = run_concurrent(
        lambda i: llm_mod.evaluate_session("behavioral", "Software Engineer", history),
        n=CONCURRENCY,
    )

    assert not errors, f"the two-pass evaluation broke under load: {errors[:1]}"
    assert len(results) == CONCURRENCY
    for r in results:
        assert 0 <= r["overall_score"] <= 10
        assert r["evaluations"]


def test_self_critique_failure_preserves_draft(fake_llm, monkeypatch):
    """An unparseable critique response must leave the draft report intact.

    _self_critique is documented as best-effort. Single-threaded so the fake's
    response ordering is deterministic: pass 1 gets valid JSON, pass 2 garbage.
    """
    monkeypatch.setattr(llm_mod, "_self_critique", REAL_SELF_CRITIQUE)
    fake_llm([EVAL_JSON, "}} definitely not json {{"])

    out = llm_mod.evaluate_session(
        "behavioral", "Software Engineer",
        [{"role": "candidate", "content": "I shipped the migration."}],
    )

    assert "Could not generate" not in out["summary"], "a bad critique sank the whole report"
    assert out["overall_score"] == 7, "draft score was not preserved"


class _DeadModel:
    """Stands in for ChatGroq when the provider is down — a 503 or a 429 burst.

    Mimics enough of the Runnable surface for `llm.bind(...) | parser` to build,
    then fails at invoke, which is where a real outage surfaces.
    """

    def bind(self, **kw):
        return self

    def __or__(self, other):
        return self

    def invoke(self, *a, **kw):
        raise RuntimeError("Groq unavailable (503)")


def test_evaluation_survives_dead_primary_without_fallback(monkeypatch):
    """A dead primary with no fallback must still return the default report.

    Regression test: _fallback_chat raises outright when FALLBACK_BASE_URL is
    unset, and it used to be called outside the inner try, so that RuntimeError
    escaped past the last-resort default and 500'd the end of an interview.
    """
    monkeypatch.setattr(llm_mod, "_make_llm", lambda *a, **kw: _DeadModel())
    monkeypatch.setattr(llm_mod, "FALLBACK_BASE_URL", "")
    monkeypatch.setattr(llm_mod, "FALLBACK_API_KEY", "")

    out = llm_mod.evaluate_session(
        "behavioral", "Software Engineer",
        [{"role": "candidate", "content": "I led a migration."}],
    )

    assert out["overall_score"] == 5
    assert "Could not generate" in out["summary"]


def test_evaluation_uses_fallback_when_primary_dies(monkeypatch):
    """A configured, working fallback must still produce a real report.

    Guards the happy path through the same try block the fix restructured —
    broadening the except must not swallow a fallback that actually worked.
    """
    monkeypatch.setattr(llm_mod, "_make_llm", lambda *a, **kw: _DeadModel())
    monkeypatch.setattr(llm_mod, "FALLBACK_BASE_URL", "https://fallback.invalid/v1")
    monkeypatch.setattr(llm_mod, "FALLBACK_API_KEY", "fallback-key")
    monkeypatch.setattr(llm_mod, "_fallback_chat", lambda *a, **kw: EVAL_JSON)

    out = llm_mod.evaluate_session(
        "behavioral", "Software Engineer",
        [{"role": "candidate", "content": "I led a migration."}],
    )

    assert "Could not generate" not in out["summary"], "a working fallback was discarded"
    assert out["overall_score"] == 7


def test_evaluation_survives_configured_fallback_timing_out(monkeypatch):
    """A configured fallback that times out must still yield the default report.

    This is the deployment-realistic shape of the bug: FALLBACK_BASE_URL *is*
    set in prod, so the unconfigured case never fires there. But the fallback is
    only ever reached when the primary is already failing, and a provider
    outage or a load burst tends to hit both — so the fallback timing out is
    correlated with, not independent of, the thing that summoned it.

    Drives the real _fallback_chat with its network severed, since the raise
    happens inside httpx, not in code we can stub around.
    """
    monkeypatch.setattr(llm_mod, "_make_llm", lambda *a, **kw: _DeadModel())
    monkeypatch.setattr(llm_mod, "FALLBACK_BASE_URL", "https://fallback.invalid/v1")
    monkeypatch.setattr(llm_mod, "FALLBACK_API_KEY", "fallback-key")

    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("fallback timed out")

    # Overrides the recording stub from assert_no_network, so this severed call
    # is not counted as an escape.
    monkeypatch.setattr(httpx, "post", timeout)

    out = llm_mod.evaluate_session(
        "behavioral", "Software Engineer",
        [{"role": "candidate", "content": "I led a migration."}],
    )

    assert out["overall_score"] == 5
    assert "Could not generate" in out["summary"]


def test_evaluation_survives_fallback_returning_garbage(monkeypatch):
    """An unparseable fallback response must degrade to the default report."""
    monkeypatch.setattr(llm_mod, "_make_llm", lambda *a, **kw: _DeadModel())
    monkeypatch.setattr(llm_mod, "FALLBACK_BASE_URL", "https://fallback.invalid/v1")
    monkeypatch.setattr(llm_mod, "FALLBACK_API_KEY", "fallback-key")
    monkeypatch.setattr(llm_mod, "_fallback_chat", lambda *a, **kw: "not json at all")

    out = llm_mod.evaluate_session(
        "behavioral", "Software Engineer",
        [{"role": "candidate", "content": "I led a migration."}],
    )

    assert out["overall_score"] == 5
    assert "Could not generate" in out["summary"]


# ── Guardrails ────────────────────────────────────────────────────────────────

LEAKY_DRAFTS = {
    "technical": [
        "Your solution is O(n log n), which is optimal here.",
        "The time complexity is O(1) so you're done.",
        "That runs in linear time, nice work.",
    ],
    "system-design": [
        "You should use Redis for the cache layer.",
        "I'd recommend sharding by tenant id.",
        "The best approach would be to add a message queue.",
    ],
}


@pytest.mark.parametrize("track", ["technical", "system-design"])
def test_guardrail_never_leaks_under_load(track, monkeypatch):
    """sanitize must never return a leaking draft, however many run at once.

    The regenerate callback returns a draft that also leaks, forcing the path
    all the way through to the canned fallback question.
    """
    monkeypatch.setattr(guardrail_mod, "_llm_judge", lambda text, track: False)
    drafts = LEAKY_DRAFTS[track]

    def sanitize(i):
        draft = drafts[i % len(drafts)]
        return guardrail_mod.sanitize(
            draft, track, regenerate_fn=lambda: drafts[(i + 1) % len(drafts)]
        )

    results, errors = run_concurrent(sanitize, n=CONCURRENCY * 2)

    assert not errors, f"guardrail raised under load: {errors[:1]}"
    for out in results:
        assert not guardrail_mod.violates(out, track), f"guardrail leaked: {out!r}"
        assert out in guardrail_mod._FALLBACK_QUESTIONS[track], (
            f"expected a fallback question, got {out!r}"
        )


def test_guardrail_judge_fails_open_when_network_dies(monkeypatch):
    """A judge whose Groq call times out must not block the interview.

    The judge is the first thing to time out under load. Failing open is the
    documented behaviour (see the guardrail module docstring), so a clean draft
    should still reach the candidate. This drives the real _llm_judge with its
    network call severed, rather than stubbing the judge itself — the swallow
    happens inside _llm_judge, so stubbing it would test nothing.
    """
    monkeypatch.setattr(guardrail_mod, "_llm_judge", REAL_LLM_JUDGE)

    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("judge timed out")

    # Overrides the recording stub from the assert_no_network fixture, so this
    # severed call is not counted as an escape.
    monkeypatch.setattr(httpx, "post", timeout)

    clean = "What edge cases were you thinking about?"
    results, errors = run_concurrent(
        lambda i: guardrail_mod.sanitize(clean, "technical", regenerate_fn=lambda: clean),
        n=CONCURRENCY,
    )
    assert not errors, f"a timing-out judge broke sanitize: {errors[:1]}"
    assert all(r == clean for r in results)


def test_regex_scales_linearly():
    """Guards the leak regexes against catastrophic backtracking.

    The complexity patterns contain a quantified character class fed by
    interviewer text. If a nested quantifier is ever introduced, a long
    adversarial string would hang the request thread. The bound is deliberately
    loose — this is a ReDoS tripwire, not a benchmark.
    """
    adversarial = "O(" + "n log n " * 5000

    start = time.monotonic()
    guardrail_mod.violates(adversarial, "technical")
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"regex took {elapsed:.2f}s on a 40KB input — likely backtracking"
