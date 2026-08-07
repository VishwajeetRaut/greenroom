"""Unit tests for the LLM response cache and the two call sites wired to it."""
from unittest.mock import patch

import pytest

from services import llm, llm_cache, test_runner


@pytest.fixture(autouse=True)
def _clean_cache():
    llm_cache.clear()
    yield
    llm_cache.clear()


# ── core cache behaviour ─────────────────────────────────────────────────────

def test_second_identical_call_is_served_from_cache():
    calls = []

    def produce():
        calls.append(1)
        return "generated"

    first = llm_cache.cached_call("ns", ("same-input",), produce)
    second = llm_cache.cached_call("ns", ("same-input",), produce)

    assert first == second == "generated"
    assert len(calls) == 1


def test_different_inputs_do_not_collide():
    llm_cache.cached_call("ns", ("a",), lambda: "value-a")
    result = llm_cache.cached_call("ns", ("b",), lambda: "value-b")
    assert result == "value-b"


def test_namespace_separates_identical_key_parts():
    llm_cache.cached_call("ns-one", ("x",), lambda: "one")
    result = llm_cache.cached_call("ns-two", ("x",), lambda: "two")
    assert result == "two"


def test_dict_key_parts_are_order_insensitive():
    llm_cache.cached_call("ns", ({"a": 1, "b": 2},), lambda: "first")
    result = llm_cache.cached_call("ns", ({"b": 2, "a": 1},), lambda: "second")
    assert result == "first"


def test_falsy_result_is_not_cached_by_default():
    """A failed generation is transient — the next request must retry it
    rather than get a pinned failure for the whole TTL."""
    calls = []

    def produce():
        calls.append(1)
        return None

    assert llm_cache.cached_call("ns", ("k",), produce) is None
    assert llm_cache.cached_call("ns", ("k",), produce) is None
    assert len(calls) == 2


def test_falsy_result_is_cached_when_explicitly_requested():
    calls = []

    def produce():
        calls.append(1)
        return None

    llm_cache.cached_call("ns", ("k",), produce, cache_falsy=True)
    llm_cache.cached_call("ns", ("k",), produce, cache_falsy=True)
    assert len(calls) == 1


def test_disabled_cache_always_calls_through():
    calls = []
    with patch.object(llm_cache, "CACHE_ENABLED", False):
        for _ in range(3):
            llm_cache.cached_call("ns", ("k",), lambda: calls.append(1))
    assert len(calls) == 3


def test_expired_entry_is_regenerated():
    calls = []

    def produce():
        calls.append(1)
        return "value"

    llm_cache.cached_call("ns", ("k",), produce, ttl=0)
    llm_cache.cached_call("ns", ("k",), produce, ttl=0)
    assert len(calls) == 2


def test_lru_eviction_respects_max_entries():
    cache = llm_cache._ResponseCache(max_entries=2)
    with patch.object(llm_cache, "_cache", cache):
        for i in range(4):
            llm_cache.cached_call("ns", (i,), lambda: f"v{i}")
    assert cache.stats()["entries"] == 2
    assert cache.evictions == 2


def test_exception_from_produce_is_not_cached():
    with pytest.raises(RuntimeError):
        llm_cache.cached_call("ns", ("k",), lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert llm_cache.cached_call("ns", ("k",), lambda: "recovered") == "recovered"


# ── pooling ──────────────────────────────────────────────────────────────────

def test_pool_generates_until_full_then_serves_from_cache():
    calls = []

    def produce():
        calls.append(1)
        return f"greeting-{len(calls)}"

    for _ in range(3):
        llm_cache.pooled_call("ns", ("k",), produce, pool_size=3)
    assert len(calls) == 3

    for _ in range(10):
        result = llm_cache.pooled_call("ns", ("k",), produce, pool_size=3)
        assert result in {"greeting-1", "greeting-2", "greeting-3"}
    assert len(calls) == 3, "pool was full — no further generation should happen"


# ── stats ────────────────────────────────────────────────────────────────────

def test_stats_report_measured_savings():
    llm_cache.cached_call("ns", ("k",), lambda: "x" * 400, prompt_chars=800)
    llm_cache.cached_call("ns", ("k",), lambda: "x" * 400, prompt_chars=800)

    s = llm_cache.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5
    assert s["est_prompt_tokens_saved"] == 200      # 800 chars / 4
    assert s["est_completion_tokens_saved"] == 100  # 400 chars / 4


# ── call sites ───────────────────────────────────────────────────────────────

def test_run_tests_reuses_generated_cases_for_the_same_problem():
    """A candidate clicks Run tests repeatedly against one problem — only the
    first click should reach the LLM."""
    cases = [{"call": "f(1)", "expected": "1"}]
    with patch.object(test_runner, "_generate_cases_uncached", return_value=cases) as gen:
        for _ in range(5):
            assert test_runner._generate_cases("Reverse a linked list.") == cases
    gen.assert_called_once()


def test_run_tests_regenerates_for_a_different_problem():
    with patch.object(test_runner, "_generate_cases_uncached", return_value=[{"call": "f()", "expected": "1"}]) as gen:
        test_runner._generate_cases("Problem A")
        test_runner._generate_cases("Problem B")
    assert gen.call_count == 2


def test_opening_message_fills_pool_then_serves_cached():
    with patch.object(llm, "OPENING_POOL_SIZE", 2), \
         patch.object(llm, "_opening_message_uncached", side_effect=["hi one", "hi two"]) as gen:
        greetings = {llm.opening_message("technical", "backend") for _ in range(8)}
    assert gen.call_count == 2
    assert greetings == {"hi one", "hi two"}


def test_opening_message_keyed_by_track_and_role():
    with patch.object(llm, "OPENING_POOL_SIZE", 1), \
         patch.object(llm, "_opening_message_uncached", side_effect=["tech", "behavioral", "other role"]) as gen:
        assert llm.opening_message("technical", "backend") == "tech"
        assert llm.opening_message("behavioral", "backend") == "behavioral"
        assert llm.opening_message("technical", "frontend") == "other role"
        assert llm.opening_message("technical", "backend") == "tech"
    assert gen.call_count == 3
