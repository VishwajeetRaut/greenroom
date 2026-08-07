"""Unit tests for provider-reported token accounting."""
from types import SimpleNamespace

import pytest

from services import token_meter


@pytest.fixture(autouse=True)
def _clean_meter():
    token_meter.clear()
    yield
    token_meter.clear()


# ── pricing ──────────────────────────────────────────────────────────────────

def test_cost_uses_separate_input_and_output_rates():
    # llama-3.3-70b-versatile: $0.59 in / $0.79 out per 1M
    cost = token_meter.cost_usd("llama-3.3-70b-versatile", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.59 + 0.79)


def test_unknown_model_returns_none_not_zero():
    """An unpriced model must not look free in a cost matrix."""
    assert token_meter.cost_usd("some-new-model", 1000, 1000) is None


def test_unpriced_model_is_surfaced_in_stats():
    token_meter.record("turn", "groq", "some-new-model", 100, 50)
    s = token_meter.stats()
    assert s["unpriced_models"] == ["some-new-model"]
    assert s["total_cost_usd"] == 0.0
    assert s["total_input_tokens"] == 100


# ── aggregation ──────────────────────────────────────────────────────────────

def test_usage_aggregates_per_call_site():
    token_meter.record("next_question", "groq", "llama-3.3-70b-versatile", 500, 40)
    token_meter.record("next_question", "groq", "llama-3.3-70b-versatile", 600, 50)
    token_meter.record("evaluate_session", "groq", "llama-3.3-70b-versatile", 400, 300)

    s = token_meter.stats()
    assert s["total_calls"] == 3
    assert s["total_input_tokens"] == 1500
    assert s["total_output_tokens"] == 390

    by_site = {r["call_site"]: r for r in s["by_call_site"]}
    assert by_site["next_question"]["calls"] == 2
    assert by_site["next_question"]["input_tokens"] == 1100
    assert by_site["evaluate_session"]["calls"] == 1


def test_same_call_site_on_different_providers_stays_separate():
    token_meter.record("next_question", "groq", "llama-3.3-70b-versatile", 100, 10)
    token_meter.record("next_question", "fallback", "llama3.3:70b", 100, 10)
    assert len(token_meter.stats()["by_call_site"]) == 2


def test_disabled_meter_records_nothing():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(token_meter, "METER_ENABLED", False)
        token_meter.record("turn", "groq", "llama-3.3-70b-versatile", 100, 10)
    assert token_meter.stats()["total_calls"] == 0


# ── extraction: OpenAI-compatible fallback path ──────────────────────────────

def test_records_from_openai_style_response():
    token_meter.record_openai_usage("next_question", "fallback", {
        "model": "llama3.3:70b",
        "usage": {"prompt_tokens": 321, "completion_tokens": 45},
    })
    s = token_meter.stats()
    assert s["total_input_tokens"] == 321
    assert s["total_output_tokens"] == 45


def test_missing_usage_block_is_ignored_not_recorded_as_zero():
    """A provider that omits usage must leave no row — a zero-token row would
    silently understate real spend."""
    token_meter.record_openai_usage("next_question", "fallback", {"model": "x", "choices": []})
    assert token_meter.stats()["total_calls"] == 0


# ── extraction: LangChain callback path ──────────────────────────────────────

def _llm_result(llm_output=None, usage_metadata=None, model_name=None):
    generations = []
    if usage_metadata is not None:
        message = SimpleNamespace(
            usage_metadata=usage_metadata,
            response_metadata={"model_name": model_name} if model_name else {},
        )
        generations = [[SimpleNamespace(message=message)]]
    return SimpleNamespace(llm_output=llm_output, generations=generations)


def test_callback_reads_llm_output_token_usage():
    recorder = token_meter.UsageRecorder("next_question")
    recorder.on_llm_end(_llm_result(llm_output={
        "model_name": "llama-3.3-70b-versatile",
        "token_usage": {"prompt_tokens": 344, "completion_tokens": 41},
    }))
    s = token_meter.stats()
    assert s["total_input_tokens"] == 344
    assert s["by_call_site"][0]["model"] == "llama-3.3-70b-versatile"


def test_callback_falls_back_to_message_usage_metadata():
    """Which field carries usage varies by integration/version — a silently
    zero token count is worse than no count, so both paths are checked."""
    recorder = token_meter.UsageRecorder("next_question")
    recorder.on_llm_end(_llm_result(
        llm_output={},
        usage_metadata={"input_tokens": 120, "output_tokens": 8},
        model_name="llama-3.1-8b-instant",
    ))
    s = token_meter.stats()
    assert s["total_input_tokens"] == 120
    assert s["total_output_tokens"] == 8


def test_callback_with_no_usage_anywhere_records_nothing():
    token_meter.UsageRecorder("next_question").on_llm_end(_llm_result(llm_output={}))
    assert token_meter.stats()["total_calls"] == 0


def test_callback_never_raises_into_the_caller():
    """Metering must never be able to break a working LLM call."""
    token_meter.UsageRecorder("next_question").on_llm_end("not an LLMResult")
    assert token_meter.stats()["total_calls"] == 0


# ── the Azure evaluation path is metered too ─────────────────────────────────

def test_azure_evaluation_calls_are_metered():
    """Evaluation moved to Azure OpenAI after this metering was written, and
    _make_azure_llm is a separate constructor from _make_llm. Without a
    recorder attached there, the whole evaluation path — the most expensive
    call in a session — would silently record nothing.
    """
    import inspect

    from services import llm

    source = inspect.getsource(llm._make_azure_llm)
    assert "UsageRecorder" in source, "_make_azure_llm attaches no usage recorder"
    assert "call_site" in inspect.signature(llm._make_azure_llm).parameters


def test_every_evaluation_call_site_is_attributed():
    """A call site left as the default 'unattributed' is invisible in the
    per-call-site breakdown, which is the whole point of the meter."""
    import inspect

    from services import llm

    for fn in (llm.evaluate_session, llm._self_critique, llm.evaluate_diagram):
        source = inspect.getsource(fn)
        if "_make_azure_llm(" in source:
            assert "call_site=" in source, f"{fn.__name__} calls Azure without a call_site"


def test_gpt5_mini_is_reported_as_unpriced_not_free():
    """No public price is wired in for it, and an unpriced model must surface
    rather than silently look free."""
    assert token_meter.cost_usd("gpt-5-mini", 1000, 1000) is None
    token_meter.record("evaluate_session", "azure", "gpt-5-mini", 500, 300)
    stats = token_meter.stats()
    assert "gpt-5-mini" in stats["unpriced_models"]
    assert stats["total_input_tokens"] == 500
