"""
In-process session cache with per-session async locks.

Stateless by design: if a request lands on a replica that never saw this
session it is rebuilt from Supabase rather than 404-ing. Required because
the backend can run with up to 2 replicas and Azure's load balancer does not
guarantee session stickiness.
"""

from __future__ import annotations

import asyncio
import threading

from services.supabase_client import get_supabase

_sessions: dict[str, dict] = {}
_session_locks: dict[str, asyncio.Lock] = {}
_locks_guard = threading.Lock()


def get_lock(session_id: str) -> asyncio.Lock:
    # asyncio.Lock, not threading.Lock — these handlers are async. A blocking
    # lock would freeze the whole event loop if two calls for the same session
    # land concurrently. _locks_guard only protects the brief dict lookup below,
    # never held across an await.
    with _locks_guard:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        return _session_locks[session_id]


def get_session(session_id: str) -> dict | None:
    cached = _sessions.get(session_id)
    if cached:
        return cached

    sb = get_supabase()
    if not sb:
        return None

    row_resp = sb.table("sessions").select("*").eq("id", session_id).limit(1).execute()
    if not row_resp.data:
        return None
    row = row_resp.data[0]

    msgs_resp = (
        sb.table("messages")
        .select("role, content, sequence_no")
        .eq("session_id", session_id)
        .order("sequence_no")
        .execute()
    )
    history = [{"role": m["role"], "content": m["content"]} for m in (msgs_resp.data or [])]

    from services import question_bank
    assigned_question = None
    if row.get("assigned_question_id"):
        assigned_question = question_bank.get_question(row["assigned_question_id"])

    session = {
        "track": row["track"],
        "role": row.get("role") or "Software Engineer",
        "history": history,
        "user_id": row.get("user_id"),
        "assigned_question": assigned_question,
        "next_sequence_no": len(history),
    }
    _sessions[session_id] = session
    return session


def put_session(session_id: str, session: dict) -> None:
    _sessions[session_id] = session


def remove_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
    with _locks_guard:
        _session_locks.pop(session_id, None)
