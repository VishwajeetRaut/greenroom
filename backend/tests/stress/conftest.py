"""Shared machinery for the stress suite.

Two rules hold for every test in this package:

  1. No real network. The evaluation engine, the dynamic interviewer and the
     guardrail judge all call out to Groq. Driving them at load against the
     real endpoint would burn credits and hit provider rate limits, so every
     outbound seam is stubbed. `assert_no_network` is autouse and records any
     call that escapes, because both `guardrail._llm_judge` and `llm.evaluate_session`
     catch broad exceptions and fail open — an escaping call would otherwise be
     swallowed and the test would still pass while silently hitting the network.

  2. Assert invariants, not wall-clock. Timing assertions on a shared CI runner
     are flaky, so these tests check properties that must hold no matter how the
     scheduler interleaves (uniqueness, no lost writes, limits actually enforced).
     The one exception is `test_regex_scales_linearly`, which guards against
     catastrophic backtracking and needs a generous, deliberately loose bound.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import services.guardrail as guardrail_mod
import services.llm as llm_mod

# A schema-valid evaluation payload. Category scores average to 7, which is what
# _reconcile_score should rewrite overall_score to regardless of what we claim here.
EVAL_PAYLOAD = {
    "overall_score": 3,  # deliberately wrong; _reconcile_score must overwrite it
    "summary": "Communicated the approach clearly and handled the follow-up.",
    "star_analysis": {
        "situation": "Set the scene concisely.",
        "task": "Stated their own responsibility.",
        "action": "Walked through the steps taken.",
        "result": "Quantified the outcome.",
        "star_score": 7,
        "missing_elements": [],
    },
    "evaluations": [
        {"category": "Communication", "score": 8, "feedback": "Clear and structured."},
        {"category": "Technical depth", "score": 6, "feedback": "Could go deeper on trade-offs."},
    ],
}

EVAL_JSON = json.dumps(EVAL_PAYLOAD)

TRACKS = ["technical", "behavioral", "system-design"]


@pytest.fixture(autouse=True)
def assert_no_network(monkeypatch):
    """Records every outbound HTTP attempt and fails the test if any occurred.

    Yields the list so a test can inspect it, but the post-yield assertion is
    what matters: it turns a silently-swallowed real API call into a failure.
    """
    escaped: list[str] = []

    def _record(url, *args, **kwargs):
        escaped.append(str(url))
        raise RuntimeError(f"stress test attempted a real network call to {url}")

    monkeypatch.setattr(httpx, "post", _record)
    monkeypatch.setattr(httpx.Client, "post", _record)
    yield escaped
    assert not escaped, f"stress test leaked real network calls: {escaped}"


@pytest.fixture
def fake_llm(monkeypatch):
    """Swaps the Groq client for a scripted fake.

    Returns an installer: call it with the responses the chain should emit.
    FakeListChatModel cycles its list, so a single response serves any number
    of concurrent invocations.
    """

    def install(responses: list[str]):
        fake = FakeListChatModel(responses=responses)
        monkeypatch.setattr(llm_mod, "_make_llm", lambda *a, **kw: fake)
        return fake

    return install


@pytest.fixture(autouse=True)
def quiet_guardrail_judge(monkeypatch):
    """Pins guardrail layer 3 (the LLM judge) to 'clean'.

    Layer 3 is a live Groq call. Left alone it would fire on every draft the
    regex clears. Tests that care about the judge override this explicitly.
    """
    monkeypatch.setattr(guardrail_mod, "_llm_judge", lambda text, track: False)


@pytest.fixture(autouse=True)
def no_self_critique(monkeypatch):
    """Disables the evaluation engine's second LLM pass by default.

    _self_critique issues its own Groq call and returns the draft unchanged on
    any failure, so leaving it live would both hit the network and mask errors.
    test_eval_self_critique_under_load re-enables it deliberately.
    """
    monkeypatch.setattr(llm_mod, "_self_critique", lambda track, role, transcript, draft: draft)


def run_concurrent(fn, n: int, workers: int = 16):
    """Runs fn(i) for i in range(n) across a thread pool.

    Returns (results, exceptions) rather than letting failures escape, so a test
    can assert on the full picture — "18 of 20 succeeded" is a more useful
    signal than whichever exception happened to surface first.
    """
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, i) for i in range(n)]
        for f in futures:
            try:
                results.append(f.result())
            except Exception as exc:  # noqa: BLE001 - deliberately collecting all
                errors.append(exc)
    return results, errors
