"""
Session-level access controls:
  - ownership check (you can only access your own sessions)
  - concurrent session cap (max N active sessions per user)
  - idle timeout (sessions expire after M minutes of inactivity)
  - turn limit (the interview ends after N candidate answers)
  - wall-clock duration cap (the interview ends after N minutes total)

Idle and duration are different failures and end differently. An idle session
was *abandoned*, so it expires hard (410). A session that hits the turn or
duration limit was *used* — the candidate has an hour or more of work in it,
so it ends gracefully and they can still click "End session" and collect the
evaluation they earned. Expiring that with a 410 would throw the work away.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import HTTPException

from auth import AuthenticatedUser
from services.supabase_client import get_supabase

MAX_ACTIVE_SESSIONS = int(os.environ.get("MAX_ACTIVE_SESSIONS", "3"))
SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30"))

# Wall-clock cap, measured from session start and unaffected by activity.
#
# Before this existed there was no bound on how long an interview could run:
# the turn limit caps how much is *said*, and the idle timeout only measures
# gaps, so a candidate answering every 29 minutes could hold a session (and one
# of their three concurrent slots, and a replica's memory) open for the better
# part of a day. Fixing the activity-tracking bug below made that more
# reachable, not less — a candidate drawing on the system-design board now
# refreshes the idle timer indefinitely, which is correct but leaves this as
# the only thing bounding the session.
MAX_SESSION_DURATION_MINUTES = int(os.environ.get("MAX_SESSION_DURATION_MINUTES", "120"))


def check_ownership(session: dict, user: AuthenticatedUser) -> None:
    owner = session.get("user_id")
    if owner and owner != user.id:
        raise HTTPException(status_code=403, detail="You don't have access to this session")


def check_session_limit(user_id: str) -> None:
    """Rejects if the user already has MAX_ACTIVE_SESSIONS open sessions."""
    sb = get_supabase()
    if not sb:
        return
    resp = sb.table("sessions").select("id", count="exact").eq("user_id", user_id).eq("status", "active").execute()
    count = resp.count or 0
    if count >= MAX_ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"You already have {count} active session(s). "
                f"End an existing session before starting a new one."
            ),
        )


MAX_CANDIDATE_TURNS = int(os.environ.get("MAX_CANDIDATE_TURNS", "15"))


def is_turn_limit_reached(session: dict) -> bool:
    """True when the candidate has sent MAX_CANDIDATE_TURNS messages in this session."""
    turns = sum(1 for t in session["history"] if t["role"] == "candidate")
    return turns >= MAX_CANDIDATE_TURNS


def _elapsed_minutes(timestamp) -> float | None:
    """Minutes since `timestamp`, or None if it's missing or unparseable —
    callers treat None as "no opinion" rather than as expired, so a bad
    timestamp can never end a live interview."""
    if not timestamp:
        return None
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    return (datetime.now(timezone.utc) - timestamp).total_seconds() / 60


def touch(session: dict) -> None:
    """Record that the candidate did something.

    Every endpoint the candidate can reach while working must call this, not
    just the ones that add a message. Running tests, switching language and
    editing the diagram were all invisible to the idle timer, so a candidate
    who spent half an hour coding — or drawing, which is most of a
    system-design session — got a 410 on their next message despite never
    having stopped working. The diagram autosaves every two seconds and still
    counted for nothing.

    Call check_idle_timeout BEFORE this, never after: touching first would let
    any request revive a session that had already expired.
    """
    session["last_activity_at"] = datetime.now(timezone.utc).isoformat()


def is_duration_limit_reached(session: dict) -> bool:
    """True once the session has been open MAX_SESSION_DURATION_MINUTES,
    regardless of how active it's been."""
    elapsed = _elapsed_minutes(session.get("started_at"))
    return elapsed is not None and elapsed >= MAX_SESSION_DURATION_MINUTES


def check_idle_timeout(session: dict) -> None:
    """Raises 410 if the session has been idle longer than SESSION_IDLE_TIMEOUT_MINUTES."""
    elapsed_minutes = _elapsed_minutes(session.get("last_activity_at"))
    if elapsed_minutes is not None and elapsed_minutes > SESSION_IDLE_TIMEOUT_MINUTES:
        raise HTTPException(
            status_code=410,
            detail=(
                f"This session has been idle for over {SESSION_IDLE_TIMEOUT_MINUTES} minutes "
                f"and has expired. Start a new session to continue."
            ),
        )
