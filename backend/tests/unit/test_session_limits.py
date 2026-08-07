"""Session limits: idle timeout, turn limit, wall-clock duration, and the
activity tracking all three depend on.

The activity tests are the important ones. Before `touch()` existed, only
POST /message and GET /resume refreshed `last_activity_at`, so running tests,
switching language and editing the diagram were invisible to the idle timer —
a candidate who coded (or drew, which is most of a system-design session) for
half an hour got a 410 on their next message despite never having stopped
working.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from services import session_guard


def _minutes_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=n)).isoformat()


def _session(*, idle_minutes=0.0, age_minutes=0.0, candidate_turns=0):
    return {
        "last_activity_at": _minutes_ago(idle_minutes),
        "started_at": _minutes_ago(age_minutes),
        "history": [{"role": "candidate", "content": "x"} for _ in range(candidate_turns)],
    }


# ── idle timeout ─────────────────────────────────────────────────────────────

def test_active_session_passes_the_idle_check():
    session_guard.check_idle_timeout(_session(idle_minutes=5))


def test_idle_session_expires_hard():
    with pytest.raises(HTTPException) as exc:
        session_guard.check_idle_timeout(_session(idle_minutes=31))
    assert exc.value.status_code == 410


def test_missing_or_unparseable_timestamp_never_ends_a_live_interview():
    session_guard.check_idle_timeout({"last_activity_at": None})
    session_guard.check_idle_timeout({"last_activity_at": "not a timestamp"})
    session_guard.check_idle_timeout({})


# ── activity tracking ────────────────────────────────────────────────────────

def test_touch_clears_an_almost_idle_session():
    session = _session(idle_minutes=29)
    session_guard.touch(session)
    session_guard.check_idle_timeout(session)  # must not raise


def test_touch_makes_continuous_work_survive_past_the_idle_window():
    """The actual bug: 45 minutes of coding, in 5-minute bursts, with no chat
    messages at all. Every burst is a /code/test call."""
    session = _session(idle_minutes=0)
    for _ in range(9):
        session["last_activity_at"] = _minutes_ago(5)
        session_guard.check_idle_timeout(session)  # 5 min idle — fine
        session_guard.touch(session)               # ...and the work registers
    session_guard.check_idle_timeout(session)


def test_touch_uses_a_parseable_timestamp():
    session = {}
    session_guard.touch(session)
    assert session_guard._elapsed_minutes(session["last_activity_at"]) < 1


# ── turn limit ───────────────────────────────────────────────────────────────

def test_turn_limit_not_reached_below_the_cap():
    assert not session_guard.is_turn_limit_reached(_session(candidate_turns=14))


def test_turn_limit_reached_at_the_cap():
    assert session_guard.is_turn_limit_reached(_session(candidate_turns=15))


def test_only_candidate_turns_count():
    session = {"history": [{"role": "interviewer", "content": "q"} for _ in range(40)]}
    assert not session_guard.is_turn_limit_reached(session)


# ── wall-clock duration ──────────────────────────────────────────────────────

def test_fresh_session_is_within_the_duration_cap():
    assert not session_guard.is_duration_limit_reached(_session(age_minutes=1))


def test_session_at_the_cap_is_done():
    assert session_guard.is_duration_limit_reached(_session(age_minutes=120))


def test_duration_ignores_activity():
    """A candidate answering every 29 minutes could previously hold a session
    open for most of a day: the turn limit caps what's said, and the idle
    timeout only measures gaps."""
    session = _session(idle_minutes=0, age_minutes=180)
    session_guard.touch(session)
    session_guard.check_idle_timeout(session)          # not idle
    assert session_guard.is_duration_limit_reached(session)  # but out of time


def test_missing_started_at_does_not_end_the_session():
    """Sessions created before this field existed must keep working."""
    assert not session_guard.is_duration_limit_reached({"history": []})
    assert not session_guard.is_duration_limit_reached({"started_at": None})


def test_duration_cap_is_configurable(monkeypatch):
    monkeypatch.setattr(session_guard, "MAX_SESSION_DURATION_MINUTES", 10)
    assert session_guard.is_duration_limit_reached(_session(age_minutes=11))
    assert not session_guard.is_duration_limit_reached(_session(age_minutes=9))


# ── the two failure modes end differently ────────────────────────────────────

def test_idle_expires_hard_but_duration_ends_gracefully():
    """An idle session was abandoned, so it 410s. A session that ran its full
    length was used — the candidate has an interview's worth of work in it and
    must still be able to end it and collect the evaluation."""
    abandoned = _session(idle_minutes=45, age_minutes=45)
    with pytest.raises(HTTPException) as exc:
        session_guard.check_idle_timeout(abandoned)
    assert exc.value.status_code == 410

    long_running = _session(idle_minutes=1, age_minutes=125)
    session_guard.check_idle_timeout(long_running)  # no exception
    assert session_guard.is_duration_limit_reached(long_running)


# ── every candidate-reachable endpoint records activity ──────────────────────

def test_all_working_endpoints_touch_the_session():
    """A fitness check on the router: any endpoint a candidate can hit while
    working must refresh the idle timer. Forgetting one is exactly how the
    original bug happened, and it fails silently — the candidate just gets a
    410 later."""
    import inspect

    from routers import interview

    source = inspect.getsource(interview)
    for endpoint in ("run_tests", "save_diagram", "get_boilerplate", "post_message"):
        start = source.index(f"def {endpoint}(")
        # Slice to the next route decorator rather than a fixed number of
        # characters — post_message is long, and a fixed window would silently
        # stop covering it as the handler grows.
        next_route = source.find("@router.", start)
        body = source[start:next_route if next_route != -1 else len(source)]
        assert "touch(session)" in body, f"{endpoint} never refreshes last_activity_at"
