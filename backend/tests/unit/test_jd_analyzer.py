"""Unit tests for job-description analysis and JD-guided question selection."""
import json
from unittest.mock import patch

import pytest

from services import jd_analyzer, llm_cache, question_bank


@pytest.fixture(autouse=True)
def _clean_cache():
    llm_cache.clear()
    yield
    llm_cache.clear()


def _llm_reply(**overrides):
    payload = {
        "role_title": "Senior Backend Engineer",
        "seniority": "senior",
        "tech_stack": ["python", "kafka"],
        "technical_topics": ["graph", "tree"],
        "behavioral_themes": ["leadership"],
        "system_design_topics": ["messaging"],
        "focus_summary": "Distributed systems and data pipelines.",
    }
    payload.update(overrides)
    return json.dumps(payload)


# ── analyze ──────────────────────────────────────────────────────────────────

def test_no_job_description_returns_none_without_calling_the_llm():
    with patch.object(jd_analyzer, "_analyze_uncached") as gen:
        assert jd_analyzer.analyze(None) is None
        assert jd_analyzer.analyze("") is None
        assert jd_analyzer.analyze("   ") is None
    gen.assert_not_called()


def test_analyze_extracts_structured_profile():
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = _llm_reply()
        profile = jd_analyzer.analyze("We need a senior backend engineer for Kafka pipelines.")

    assert profile["role_title"] == "Senior Backend Engineer"
    assert profile["seniority"] == "senior"
    assert profile["difficulty"] == ["medium", "hard"]
    assert profile["tech_stack"] == ["python", "kafka"]


def test_analyze_is_cached_per_job_description():
    with patch.object(jd_analyzer, "_analyze_uncached", return_value={"role_title": "X"}) as gen:
        for _ in range(4):
            jd_analyzer.analyze("Same posting text")
    gen.assert_called_once()


def test_invented_topics_are_dropped():
    """The bank's topic vocabulary is fixed and inconsistent; a free-text topic
    would match no question and silently degrade to a random pick."""
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = _llm_reply(
            technical_topics=["graph", "quantum-computing", "blockchain"],
        )
        profile = jd_analyzer.analyze("A posting")
    assert profile["technical_topics"] == ["graph"]


def test_unknown_seniority_leaves_difficulty_unset():
    """An unrecognised seniority must not silently narrow the question pool."""
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = _llm_reply(seniority="wizard")
        profile = jd_analyzer.analyze("A posting")
    assert profile["seniority"] is None
    assert profile["difficulty"] is None


def test_missing_role_title_falls_back_to_the_previous_default():
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = _llm_reply(role_title="")
        profile = jd_analyzer.analyze("A posting")
    assert profile["role_title"] == "Software Engineer"


def test_unparseable_reply_returns_none_not_a_broken_profile():
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = "sorry, I can't do that"
        assert jd_analyzer.analyze("A posting") is None


def test_both_providers_failing_returns_none():
    with patch("services.llm._make_llm", side_effect=RuntimeError("groq down")), \
         patch("services.llm._fallback_chat", side_effect=RuntimeError("fallback down")):
        assert jd_analyzer.analyze("A posting") is None


def test_markdown_fenced_json_is_still_parsed():
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = f"```json\n{_llm_reply()}\n```"
        assert jd_analyzer.analyze("A posting")["seniority"] == "senior"


# ── accessors ────────────────────────────────────────────────────────────────

def test_topics_for_track_maps_to_the_right_field():
    profile = {
        "technical_topics": ["graph"],
        "behavioral_themes": ["leadership"],
        "system_design_topics": ["messaging"],
    }
    assert jd_analyzer.topics_for_track(profile, "technical") == ["graph"]
    assert jd_analyzer.topics_for_track(profile, "behavioral") == ["leadership"]
    assert jd_analyzer.topics_for_track(profile, "system-design") == ["messaging"]
    assert jd_analyzer.topics_for_track(None, "technical") == []


def test_prompt_fragment_is_empty_without_a_profile():
    """No JD must leave the interviewer prompt byte-identical to its old form."""
    assert jd_analyzer.prompt_fragment(None) == ""


def test_prompt_fragment_is_far_smaller_than_the_raw_paste():
    """The interviewer prompt is re-sent every turn, so the profile replacing
    the raw paste is the point, not a nicety."""
    raw = "We are hiring. " * 400  # ~6000 chars, near the 5000-char field cap
    with patch("services.llm._make_llm") as make_llm:
        make_llm.return_value.invoke.return_value.content = _llm_reply()
        profile = jd_analyzer.analyze(raw)
    fragment = jd_analyzer.prompt_fragment(profile)
    assert "Senior Backend Engineer" in fragment
    assert len(fragment) < len(raw) / 10


# ── JD-guided selection ──────────────────────────────────────────────────────

def test_system_design_picker_filters_by_topic():
    picked = question_bank.pick_system_design_question(topic="messaging")
    assert picked is None or picked["topic"] == "messaging"


def test_system_design_picker_returns_none_for_an_absent_topic():
    assert question_bank.pick_system_design_question(topic="not-a-real-topic") is None


def test_jd_guided_pick_widens_when_the_topic_has_no_questions():
    """A JD must be able to steer the choice but never leave the candidate
    with no question at all."""
    from routers.interview import _pick_jd_guided
    picked = _pick_jd_guided(
        question_bank.pick_system_design_question,
        topics=["not-a-real-topic"], role="Senior Backend Engineer",
    )
    assert picked is not None
    assert picked["track"] == "system-design"


def test_jd_guided_pick_prefers_the_first_matching_topic():
    from routers.interview import _pick_jd_guided
    picked = _pick_jd_guided(
        question_bank.pick_behavioral_question,
        topics=["not-a-real-topic", "conflict"], role=None,
    )
    assert picked["topic"] == "conflict"


# ── select_or_generate_question with a JD profile ────────────────────────────

_SENIOR_PROFILE = {
    "role_title": "Senior Backend Engineer",
    "seniority": "senior",
    "difficulty": ["medium", "hard"],
    "tech_stack": ["python"],
    "technical_topics": ["graph"],
    "behavioral_themes": [],
    "system_design_topics": [],
    "focus_summary": "Graph traversal at scale.",
}


def _bank(*specs):
    return [
        {"id": f"q{i}", "track": "technical", "topic": topic, "difficulty": difficulty,
         "title": f"Q{i}", "languages": ["python"], "tests": [{"call": "f()", "expected": "1"}]}
        for i, (topic, difficulty) in enumerate(specs)
    ]


@pytest.mark.asyncio
async def test_thin_intro_with_a_jd_picks_on_the_jd_topic_not_at_random():
    """A content-free intro skips the LLM path entirely (it has nothing to
    reason about). Before the JD reached selection, that meant a purely random
    problem even when the JD was specific."""
    from services import question_generator
    questions = _bank(("graph", "medium"), ("array", "easy"), ("array", "easy"))

    with patch("services.question_bank._all_questions", return_value=questions), \
         patch.object(question_generator, "_ask_llm") as ask:
        picked = await question_generator.select_or_generate_question(
            "Senior Backend Engineer", candidate_intro="next", jd_profile=_SENIOR_PROFILE,
        )

    ask.assert_not_called()
    assert picked["topic"] == "graph"


@pytest.mark.asyncio
async def test_jd_selection_widens_rather_than_returning_nothing():
    """The JD asks for a graph topic the bank doesn't have. A JD must never be
    able to leave the candidate with no problem.

    Difficulty is deliberately not part of this any more: seniority is applied
    by question_bank's weighted pick (driven by the analysed role title), not
    by filtering here, so there is only one narrowing dimension left to widen.
    """
    from services import question_generator
    questions = _bank(("array", "easy"), ("string", "easy"))

    with patch("services.question_bank._all_questions", return_value=questions), \
         patch.object(question_generator, "_ask_llm") as ask:
        picked = await question_generator.select_or_generate_question(
            "Senior Backend Engineer", candidate_intro="next", jd_profile=_SENIOR_PROFILE,
        )

    ask.assert_not_called()
    assert picked is not None
    assert picked["track"] == "technical"


@pytest.mark.asyncio
async def test_no_jd_profile_leaves_selection_behaviour_unchanged():
    from services import question_generator
    questions = _bank(("array", "easy"))

    with patch("services.question_bank._all_questions", return_value=questions), \
         patch.object(question_generator, "_ask_llm") as ask:
        picked = await question_generator.select_or_generate_question(
            "Software Engineer", candidate_intro="next", jd_profile=None,
        )

    ask.assert_not_called()
    assert picked["topic"] == "array"


@pytest.mark.asyncio
async def test_jd_requirements_reach_the_selection_prompt():
    """With a real intro the LLM path runs — the JD analysis has to actually
    be in the prompt, not just in the fallback picker."""
    from services import question_generator
    questions = _bank(*[("graph", "medium")] * 10)

    with patch("services.question_bank._all_questions", return_value=questions), \
         patch.object(question_generator, "_ask_llm",
                      return_value=json.dumps({"action": "use_existing", "id": "q0"})) as ask, \
         patch("services.question_bank.get_question", return_value=questions[0]):
        await question_generator.select_or_generate_question(
            "Senior Backend Engineer",
            candidate_intro="I have been a backend engineer for six years working on Kafka pipelines.",
            jd_profile=_SENIOR_PROFILE,
        )

    system_prompt = ask.call_args[0][0]
    assert "Seniority: senior" in system_prompt
    assert "graph" in system_prompt
    assert "Graph traversal at scale." in system_prompt
