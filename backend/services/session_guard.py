"""
Session-level access controls:
  - ownership check (you can only access your own sessions)
  - concurrent session cap (max N active sessions per user)
  - idle timeout (sessions expire after M minutes of inactivity)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import HTTPException
from postgrest.types import CountMethod

from auth import AuthenticatedUser
from services.supabase_client import get_supabase

MAX_ACTIVE_SESSIONS = int(os.environ.get("MAX_ACTIVE_SESSIONS", "3"))
SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30"))


def check_ownership(session: dict, user: AuthenticatedUser) -> None:
    owner = session.get("user_id")
    if owner and owner != user.id:
        raise HTTPException(status_code=403, detail="You don't have access to this session")


def check_session_limit(user_id: str) -> None:
    """Rejects if the user already has MAX_ACTIVE_SESSIONS open sessions."""
    sb = get_supabase()
    if not sb:
        return
    resp = sb.table("sessions").select("id", count=CountMethod.exact).eq("user_id", user_id).eq("status", "active").execute()
    count = resp.count or 0
    if count >= MAX_ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"You already have {count} active session(s). "
                f"End an existing session before starting a new one."
            ),
        )


def check_idle_timeout(session: dict) -> None:
    """Raises 410 if the session has been idle longer than SESSION_IDLE_TIMEOUT_MINUTES."""
    last_activity = session.get("last_activity_at")
    if not last_activity:
        return
    if isinstance(last_activity, str):
        try:
            last_activity = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
        except ValueError:
            return
    elapsed_minutes = (datetime.now(timezone.utc) - last_activity).total_seconds() / 60
    if elapsed_minutes > SESSION_IDLE_TIMEOUT_MINUTES:
        raise HTTPException(
            status_code=410,
            detail=(
                f"This session has been idle for over {SESSION_IDLE_TIMEOUT_MINUTES} minutes "
                f"and has expired. Start a new session to continue."
            ),
        )
