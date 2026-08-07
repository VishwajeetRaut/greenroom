"""Transcript sizing, compaction and chunked evaluation.

Grounded in a measured failure: a 15-turn session carrying an ~8KB file on
every turn produced a 128,000-character transcript that Groq billed at 55,467
tokens — 55% of the free tier's 100,000-token daily allowance in one call. It
returned 429, and because the fallback in evaluate_session sat outside the
inner try, the exception escaped and POST /interview/end returned a 500. The
candidate finished the interview and got nothing.
"""
from unittest.mock import patch

import pytest

from services import llm
from services import transcript as tb


def _code_turn(index: int, code_size: int = 8000) -> dict:
    code = "def solve(nums):\n" + ("    x = 1  # padding\n" * (code_size // 20))
    return {"role": "candidate",
            "content": f"Attempt {index}.\n\n[Candidate's current code]\n{code}"}


def _long_history(turns: int = 15) -> list[dict]:
    history = [{"role": "interviewer", "content": "Tell me about yourself."}]
    for i in range(turns):
        history.append(_code_turn(i))
        history.append({"role": "interviewer", "content": f"Follow-up {i}: what's the complexity?"})
    return history


# ── estimation ───────────────────────────────────────────────────────────────

def test_token_estimate_is_pessimistic_for_code():
    """chars/4 understates code-heavy text by ~70% against Groq's own
    accounting; under-estimating here means a failed evaluation."""
    text = "x" * 2300
    assert tb.estimate_tokens(text) == 1000


# ── compaction ───────────────────────────────────────────────────────────────

def test_compaction_keeps_only_the_latest_code_revision():
    history = _long_history(5)
    compacted = tb.compact(history)
    rendered = tb.render(compacted)
    assert rendered.count("superseded below") == 4
    assert "def solve(nums):" in rendered  # the final revision survives in full


def test_compaction_is_a_large_reduction():
    history = _long_history(15)
    before = len(tb.render(history))
    after = len(tb.render(tb.compact(history)))
    assert after < before / 5, f"{before} -> {after}"


def test_compaction_leaves_conversation_untouched():
    history = [
        {"role": "interviewer", "content": "What's your background?"},
        {"role": "candidate", "content": "Backend engineer, three years."},
    ]
    assert tb.compact(history) == history


def test_compaction_handles_diagram_blocks_too():
    """System-design sessions re-send the serialised board every turn, exactly
    like the technical track re-sends code."""
    history = [
        {"role": "candidate", "content": "v1\n\n[Architecture diagram]\nComponents: a, b"},
        {"role": "candidate", "content": "v2\n\n[Architecture diagram]\nComponents: a, b, c"},
    ]
    rendered = tb.render(tb.compact(history))
    assert "superseded below" in rendered
    assert "Components: a, b, c" in rendered


# ── build ────────────────────────────────────────────────────────────────────

def test_short_transcript_is_sent_verbatim():
    """A session that already worked must be completely unaffected."""
    history = [
        {"role": "interviewer", "content": "Hello"},
        {"role": "candidate", "content": "Hi, I'm a backend engineer."},
    ]
    built, fits = tb.build(history)
    assert fits
    assert built == tb.render(history)


def test_oversized_transcript_is_compacted_and_then_fits():
    built, fits = tb.build(_long_history(15))
    assert fits
    assert "superseded below" in built


def test_build_reports_when_even_compaction_is_not_enough():
    history = _long_history(15)
    built, fits = tb.build(history, max_tokens=200)
    assert not fits
    assert built  # still returns the best effort for the caller to chunk


# ── chunking ─────────────────────────────────────────────────────────────────

def test_chunks_each_fit_the_budget():
    budget = 2000
    for chunk in tb.chunks(_long_history(15), max_tokens=budget):
        assert tb.estimate_tokens(chunk) <= budget * 1.1


def test_chunks_break_on_turn_boundaries():
    for chunk in tb.chunks(_long_history(6), max_tokens=1500):
        for line in chunk.splitlines():
            if line.startswith(("Interviewer:", "Candidate:")):
                continue  # a new turn — fine
    # every chunk starts with a speaker label
    assert all(c.startswith(("Interviewer:", "Candidate:")) for c in tb.chunks(_long_history(6), max_tokens=1500))


def test_a_single_oversized_turn_is_truncated_not_dropped():
    """Losing the tail of one 100KB paste beats failing the whole evaluation."""
    history = [{"role": "candidate", "content": "x" * 200_000}]
    out = tb.chunks(history, max_tokens=500)
    assert len(out) == 1
    assert "truncated" in out[0]


def test_chunking_covers_every_turn():
    history = _long_history(8)
    joined = " ".join(tb.chunks(history, max_tokens=2000))
    for i in range(8):
        assert f"Follow-up {i}:" in joined


def test_no_chunks_for_empty_history():
    assert tb.chunks([]) == []


# ── evaluate_session never raises ────────────────────────────────────────────

def test_both_providers_failing_returns_the_default_not_an_exception():
    """The measured bug: Groq 429s on an oversized transcript, the code tries
    the fallback, the fallback also fails, and the exception escaped — turning
    POST /interview/end into a 500 and losing the candidate's evaluation."""
    rate_limited = RuntimeError("rate limit")
    rate_limited.status_code = 429

    with patch.object(llm, "_make_azure_llm", side_effect=rate_limited), \
         patch.object(llm, "_fallback_chat", side_effect=OSError("fallback unreachable")):
        result = llm.evaluate_session("technical", "backend", [{"role": "candidate", "content": "hi"}])

    assert result["overall_score"] == 5
    assert "Could not generate" in result["summary"]
    assert result["evaluations"] == []


def test_non_retryable_error_does_not_call_the_fallback():
    bad_request = RuntimeError("bad request")
    bad_request.status_code = 400

    with patch.object(llm, "_make_azure_llm", side_effect=bad_request), \
         patch.object(llm, "_fallback_chat") as fallback:
        result = llm.evaluate_session("technical", "backend", [{"role": "candidate", "content": "hi"}])

    fallback.assert_not_called()
    assert "Could not generate" in result["summary"]


def test_oversized_session_goes_through_the_chunked_path():
    history = _long_history(15)
    notes = '{"strengths": ["clear"], "weaknesses": [], "notable_quotes": [], "topics_covered": ["arrays"]}'
    final = {
        "overall_score": 7, "summary": "Solid.",
        "star_analysis": {"situation": "-", "task": "-", "action": "-", "result": "-",
                           "star_score": 6, "missing_elements": []},
        "evaluations": [{"category": "clarity", "score": 7, "feedback": "good"}],
    }

    with patch.object(tb, "MAX_TRANSCRIPT_TOKENS", 1500), \
         patch.object(llm, "_make_azure_llm") as make_llm, \
         patch.object(llm, "_evaluate_transcript", return_value=dict(final)) as reduce_call, \
         patch.object(llm, "_self_critique", side_effect=lambda t, r, tr, d: d):
        make_llm.return_value.bind.return_value.invoke.return_value.content = notes
        result = llm.evaluate_session("technical", "backend", history)

    assert make_llm.call_count > 1, "expected one map call per segment"
    reduce_call.assert_called_once()
    assert result["overall_score"] == 7


def test_one_bad_segment_does_not_sink_the_report():
    history = _long_history(15)
    notes = '{"strengths": ["clear"], "weaknesses": [], "notable_quotes": [], "topics_covered": []}'
    calls = {"n": 0}

    def flaky_bind(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("segment blew up")

        class _R:
            content = notes

        class _B:
            def invoke(self, _):
                return _R()
        return _B()

    with patch.object(tb, "MAX_TRANSCRIPT_TOKENS", 1500), \
         patch.object(llm, "_make_azure_llm") as make_llm, \
         patch.object(llm, "_evaluate_transcript", return_value={"overall_score": 6, "summary": "ok",
                                                                 "star_analysis": {}, "evaluations": []}), \
         patch.object(llm, "_self_critique", side_effect=lambda t, r, tr, d: d):
        make_llm.return_value.bind.side_effect = flaky_bind
        result = llm.evaluate_session("technical", "backend", history)

    assert result["summary"] == "ok"


@pytest.mark.parametrize("history", [[], [{"role": "candidate", "content": ""}]])
def test_empty_history_still_produces_a_report(history):
    with patch.object(llm, "_make_azure_llm", side_effect=OSError("down")), \
         patch.object(llm, "_fallback_chat", side_effect=OSError("down")):
        assert llm.evaluate_session("technical", "backend", history)["overall_score"] == 5
