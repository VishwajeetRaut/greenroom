"""Unit tests for ad-hoc (non-curated-bank) Java/C++ test-harness generation."""
import pytest

from services import adhoc_harness, harness_generator

# ── _infer_method_name ───────────────────────────────────────────────────────

def test_infer_plain_function_call():
    assert adhoc_harness._infer_method_name("canJump(nums=[2,3,1,1,4])") == "canJump"


def test_infer_plain_positional_call():
    assert adhoc_harness._infer_method_name("two_sum([2, 7, 11, 15], 9)") == "two_sum"


def test_infer_stateful_constructor_call():
    call = "obj = LRUCache(2); obj.put(1,1); obj.get(1)"
    assert adhoc_harness._infer_method_name(call) == "LRUCache"


def test_infer_returns_none_for_unparseable_call():
    assert adhoc_harness._infer_method_name("not a call at all") is None


# ── get_or_generate / get_or_generate_signature ─────────────────────────────

CASES = [
    {"call": "canJump(nums=[2,3,1,1,4])", "expected": "True"},
    {"call": "canJump(nums=[3,2,1,0,4])", "expected": "False"},
    {"call": "canJump(nums=[1])", "expected": "True"},
]


@pytest.fixture(autouse=True)
def _clear_cache():
    adhoc_harness._cache.clear()
    yield
    adhoc_harness._cache.clear()


@pytest.mark.asyncio
async def test_unsupported_language_returns_none():
    assert await adhoc_harness.get_or_generate("python", "problem text", CASES) is None
    assert await adhoc_harness.get_or_generate_signature("node", "problem text", CASES) is None


@pytest.mark.asyncio
async def test_empty_cases_returns_none():
    assert await adhoc_harness.get_or_generate("java", "problem text", []) is None


@pytest.mark.asyncio
async def test_get_or_generate_success_on_first_attempt(monkeypatch):
    spec = {"boilerplate": "class Solution {}", "solution": "class Solution { real impl }", "harness": "class Main {}"}

    def fake_generate(language, question, feedback):
        assert question["function_name"] == "canJump"
        assert feedback is None
        return spec

    async def fake_verify(language, spec_arg, n_tests):
        assert spec_arg == spec
        assert n_tests == 3
        return True, ""

    monkeypatch.setattr(harness_generator, "_generate", fake_generate)
    monkeypatch.setattr(harness_generator, "_verify", fake_verify)

    result = await adhoc_harness.get_or_generate("java", "Jump Game problem", CASES)
    assert result == {"boilerplate": spec["boilerplate"], "harness": spec["harness"]}


@pytest.mark.asyncio
async def test_get_or_generate_retries_with_feedback_then_succeeds(monkeypatch):
    good_spec = {"boilerplate": "b", "solution": "s", "harness": "h"}
    attempts = []

    def fake_generate(language, question, feedback):
        attempts.append(feedback)
        return good_spec

    async def fake_verify(language, spec_arg, n_tests):
        # Fail on first call, succeed on second.
        if len(attempts) == 1:
            return False, "compile error: missing semicolon"
        return True, ""

    monkeypatch.setattr(harness_generator, "_generate", fake_generate)
    monkeypatch.setattr(harness_generator, "_verify", fake_verify)

    result = await adhoc_harness.get_or_generate("cpp", "Jump Game problem", CASES)
    assert result is not None
    assert attempts == [None, "compile error: missing semicolon"]


@pytest.mark.asyncio
async def test_get_or_generate_exhausts_attempts_and_returns_none(monkeypatch):
    def fake_generate(language, question, feedback):
        return {"boilerplate": "b", "solution": "s", "harness": "h"}

    async def fake_verify(language, spec_arg, n_tests):
        return False, "always fails"

    monkeypatch.setattr(harness_generator, "_generate", fake_generate)
    monkeypatch.setattr(harness_generator, "_verify", fake_verify)

    result = await adhoc_harness.get_or_generate("java", "Jump Game problem", CASES)
    assert result is None


@pytest.mark.asyncio
async def test_get_or_generate_result_is_cached(monkeypatch):
    call_count = 0

    def fake_generate(language, question, feedback):
        nonlocal call_count
        call_count += 1
        return {"boilerplate": "b", "solution": "s", "harness": "h"}

    async def fake_verify(language, spec_arg, n_tests):
        return True, ""

    monkeypatch.setattr(harness_generator, "_generate", fake_generate)
    monkeypatch.setattr(harness_generator, "_verify", fake_verify)

    r1 = await adhoc_harness.get_or_generate("java", "Jump Game problem", CASES)
    r2 = await adhoc_harness.get_or_generate("java", "Jump Game problem", CASES)
    assert r1 == r2
    assert call_count == 1  # second call served from cache, no regeneration


@pytest.mark.asyncio
async def test_get_or_generate_signature_success(monkeypatch):
    def fake_generate_signature(language, method_name, question):
        assert method_name == "canJump"
        return "class Solution { public boolean canJump(int[] nums) { return false; } }"

    monkeypatch.setattr(harness_generator, "_generate_signature", fake_generate_signature)

    result = await adhoc_harness.get_or_generate_signature("java", "Jump Game problem", CASES)
    assert result is not None
    assert "canJump" in result


@pytest.mark.asyncio
async def test_get_or_generate_signature_none_when_generation_fails(monkeypatch):
    def fake_generate_signature(language, method_name, question):
        return None

    monkeypatch.setattr(harness_generator, "_generate_signature", fake_generate_signature)

    result = await adhoc_harness.get_or_generate_signature("cpp", "Jump Game problem", CASES)
    assert result is None
