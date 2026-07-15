"""Regression tests for what /message leaves behind when the interviewer fails.

The failure that matters here is llm.next_question raising — both LLM providers
down, which is the same outage that reaches the evaluation engine. What made it
costly was not the error itself but the state it left: the candidate's turn was
already persisted, the UI invited them to repeat it, and the retry appended it
again. The transcript llm.evaluate_session later grades ended up with two
candidate turns in a row and no interviewer turn between them.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import routers.interview as router_mod
from models import MessageRequest

SESSION_ID = "sess-retry"


class FakeUser:
    id = "user-1"
    email = "candidate@example.com"


class NoLock:
    """session_lock returns an async context manager; the lock itself isn't
    under test here."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


QUESTION = {
    "id": "q-two-sum",
    "title": "Two Sum",
    "prompt": "Return indices of two numbers adding to target.",
    "function_name": "twoSum",
    "difficulty": "easy",
    "constraints": ["1 <= n <= 10^4"],
    "examples": [],
    "tests": [{"call": "twoSum([2,7],9)", "expected": "[0,1]"}],
}


@pytest.fixture
def session():
    return {
        "track": "technical",
        "role": "Software Engineer",
        "history": [{"role": "interviewer", "content": "Tell me about yourself."}],
        "user_id": "user-1",
        "assigned_question": None,
        "next_sequence_no": 1,
        "last_activity_at": None,
    }


@pytest.fixture
def router(monkeypatch, session):
    """Stubs everything except the code under test, and records what was
    persisted so a test can tell in-memory state from committed state."""
    persisted: list[tuple] = []

    async def ok_question(*a, **kw):
        return QUESTION

    monkeypatch.setattr(router_mod, "session_lock", lambda sid: NoLock())
    monkeypatch.setattr(router_mod, "get_session", lambda sid: session)
    monkeypatch.setattr(router_mod, "check_ownership", lambda s, u: None)
    monkeypatch.setattr(router_mod, "check_idle_timeout", lambda s: None)
    monkeypatch.setattr(router_mod, "is_turn_limit_reached", lambda s: False)
    monkeypatch.setattr(router_mod, "check_rate_limit", lambda *a, **kw: None)
    monkeypatch.setattr(router_mod, "persist_message",
                        lambda sid, role, content, seq: persisted.append((seq, role, content)))
    monkeypatch.setattr(router_mod, "persist_assigned_question", lambda *a: None)
    monkeypatch.setattr(router_mod, "now", lambda: "2026-07-15T00:00:00Z")
    monkeypatch.setattr(router_mod.question_generator, "select_or_generate_question", ok_question)
    return persisted


def dead_interviewer(*a, **kw):
    raise RuntimeError("Groq 429 and the fallback timed out")


async def test_failed_reply_returns_503_not_500(router, monkeypatch, session):
    """A provider outage is a retryable condition, not an internal error."""
    monkeypatch.setattr(router_mod.llm, "next_question", dead_interviewer)

    with pytest.raises(HTTPException) as exc:
        await router_mod.post_message(
            MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
        )

    assert exc.value.status_code == 503
    assert "not recorded" in exc.value.detail


async def test_failed_reply_records_nothing(router, monkeypatch, session):
    """Neither turn may be committed if the interviewer never replied."""
    monkeypatch.setattr(router_mod.llm, "next_question", dead_interviewer)

    with pytest.raises(HTTPException):
        await router_mod.post_message(
            MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
        )

    assert router == [], f"persisted despite failing to reply: {router}"
    assert [t["role"] for t in session["history"]] == ["interviewer"], (
        "the candidate's turn was left in history after a failed reply"
    )
    assert session["next_sequence_no"] == 1, "sequence number advanced on a failed turn"


async def test_retry_after_failure_does_not_duplicate_the_turn(router, monkeypatch, session):
    """The core regression: the UI invites a retry, which must not double the turn.

    Two candidate turns in a row with no interviewer turn between them is a
    corrupted transcript, and evaluate_session grades it.
    """
    monkeypatch.setattr(router_mod.llm, "next_question", dead_interviewer)
    with pytest.raises(HTTPException):
        await router_mod.post_message(
            MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
        )

    monkeypatch.setattr(router_mod.llm, "next_question",
                        lambda *a, **kw: "What's the time complexity?")
    await router_mod.post_message(
        MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
    )

    roles = [t["role"] for t in session["history"]]
    assert roles == ["interviewer", "candidate", "interviewer"], f"transcript is {roles}"
    assert sum(1 for r in roles if r == "candidate") == 1, "the candidate's turn was duplicated"

    persisted_roles = [role for _, role, _ in router]
    assert persisted_roles == ["candidate", "interviewer"]
    assert [seq for seq, _, _ in router] == [1, 2], "sequence numbers are not contiguous"


async def test_retry_after_failure_still_delivers_question_context(router, monkeypatch, session):
    """The constraints panel must survive a failed first attempt.

    is_first_reply used to be inferred from `assigned_question is None`. The
    failed attempt had already assigned the question, so every later reply
    looked like a non-first one and the context never reached the candidate —
    who then solved a problem whose constraints they were never shown.
    """
    monkeypatch.setattr(router_mod.llm, "next_question", dead_interviewer)
    with pytest.raises(HTTPException):
        await router_mod.post_message(
            MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
        )
    assert session["assigned_question"] is not None, "question was not retained across the failure"

    monkeypatch.setattr(router_mod.llm, "next_question", lambda *a, **kw: "Here's the problem.")
    resp = await router_mod.post_message(
        MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
    )

    assert resp.question_context is not None, "constraints panel never reached the candidate"
    assert resp.question_context.id == "q-two-sum"
    assert resp.question_context.constraints == ["1 <= n <= 10^4"]


async def test_question_context_is_delivered_only_once(router, monkeypatch, session):
    """Re-sending context on every reply would refetch boilerplate each turn.

    The frontend's handleQuestionAssigned also triggers fetchBoilerplate, which
    can generate and sandbox-verify a harness — not something to repeat per turn.
    """
    monkeypatch.setattr(router_mod.llm, "next_question", lambda *a, **kw: "First question.")
    first = await router_mod.post_message(
        MessageRequest(session_id=SESSION_ID, message="Hi, I'm a backend dev."), FakeUser()
    )
    assert first.question_context is not None

    second = await router_mod.post_message(
        MessageRequest(session_id=SESSION_ID, message="I'd sort it first."), FakeUser()
    )
    assert second.question_context is None, "context re-sent on a later turn"


async def test_successful_turn_is_unchanged(router, monkeypatch, session):
    """The happy path must behave exactly as before."""
    monkeypatch.setattr(router_mod.llm, "next_question", lambda *a, **kw: "Why a hash map?")

    resp = await router_mod.post_message(
        MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
    )

    assert resp.question == "Why a hash map?"
    assert [t["role"] for t in session["history"]] == ["interviewer", "candidate", "interviewer"]
    assert [(seq, role) for seq, role, _ in router] == [(1, "candidate"), (2, "interviewer")]
    assert session["next_sequence_no"] == 3
    assert session["last_activity_at"] == "2026-07-15T00:00:00Z"


async def test_http_exceptions_pass_through_untouched(router, monkeypatch, session):
    """Guard rails that raise their own HTTPException must not become a 503.

    A 429 from a downstream guard is a different condition with a different
    client contract; swallowing it into 503 would mislabel it.
    """
    def rate_limited(*a, **kw):
        raise HTTPException(status_code=429, detail="Too many requests")

    monkeypatch.setattr(router_mod.llm, "next_question", rate_limited)

    with pytest.raises(HTTPException) as exc:
        await router_mod.post_message(
            MessageRequest(session_id=SESSION_ID, message="I'd use a hash map."), FakeUser()
        )

    assert exc.value.status_code == 429
    assert [t["role"] for t in session["history"]] == ["interviewer"], "turn not rolled back"
