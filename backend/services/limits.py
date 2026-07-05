"""
Business-rule limits: session count per user and message turns per session.
"""

from __future__ import annotations

from fastapi import HTTPException, status

MAX_SESSIONS_PER_USER = 10
MAX_CANDIDATE_TURNS = 15


def check_session_count(user_id: str) -> None:
    """Raises 429 if the user has hit MAX_SESSIONS_PER_USER."""
    from services.supabase_client import get_supabase
    sb = get_supabase()
    if not sb:
        return
    try:
        resp = sb.table("sessions").select("id", count="exact").eq("user_id", user_id).execute()
        if (resp.count or 0) >= MAX_SESSIONS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"You've reached the {MAX_SESSIONS_PER_USER}-session limit. "
                    "Delete old sessions from your dashboard to start a new one."
                ),
            )
    except HTTPException:
        raise
    except Exception:
        pass  # allow through if Supabase is unreachable


def candidate_turns(session: dict) -> int:
    return sum(1 for t in session["history"] if t["role"] == "candidate")


def is_session_full(session: dict) -> bool:
    return candidate_turns(session) >= MAX_CANDIDATE_TURNS
