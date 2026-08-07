"""
Process-local session cache + rebuild-from-Supabase logic.

SESSIONS is the in-memory map; it is always consistent with the Supabase
`sessions` table because every mutation is persisted synchronously before the
response is returned. On a cache miss (_get_session called for an unknown
session_id), we rebuild from the DB so this replica can serve requests that
were started on a sibling replica.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone

from services import question_bank
from services.supabase_client import get_supabase

SESSIONS: dict[str, dict] = {}
_session_locks: dict[str, asyncio.Lock] = {}
_locks_guard = threading.Lock()


def session_lock(session_id: str) -> asyncio.Lock:
    with _locks_guard:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        return _session_locks[session_id]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_session(session_id: str) -> dict | None:
    """Returns cached session or rebuilds it from Supabase. None only if it truly doesn't exist."""
    cached = SESSIONS.get(session_id)
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
        .select("role, content, sequence_no, created_at")
        .eq("session_id", session_id)
        .order("sequence_no")
        .execute()
    )
    history = [{"role": m["role"], "content": m["content"]} for m in (msgs_resp.data or [])]

    assigned_question = None
    if row.get("assigned_question_id"):
        assigned_question = question_bank.get_question(row["assigned_question_id"])

    # last_activity_at: most recent message timestamp, or session created_at
    last_activity = row.get("created_at")
    if msgs_resp.data:
        last_activity = msgs_resp.data[-1].get("created_at", last_activity)

    session = {
        "track": row["track"],
        "role": row.get("role") or "Software Engineer",
        "history": history,
        "user_id": row.get("user_id"),
        "assigned_question": assigned_question,
        "next_sequence_no": len(history),
        "last_activity_at": last_activity,
        # Wall-clock cap is measured from here. Rebuilt from the row rather
        # than reset to now(), or a replica restart would silently hand every
        # in-flight session a fresh duration budget.
        "started_at": row.get("created_at"),
        "status": row.get("status") or "active",
        "diagram_elements": row.get("diagram_elements") or [],
        # Only the current one is recoverable from a rebuild — full skip
        # history isn't persisted (see "Next question" in routers/interview.py) —
        # so a "Next question" click right after a process restart could in
        # theory re-serve a question skipped earlier in the same session.
        # Rare edge case, not worth a migration for.
        "asked_question_ids": {row["assigned_question_id"]} if row.get("assigned_question_id") else set(),
        "candidate_intro": next((m["content"] for m in history if m["role"] == "candidate"), ""),
    }
    SESSIONS[session_id] = session
    return session


def evict(session_id: str) -> None:
    """Remove session from the in-memory cache and clean up its lock."""
    SESSIONS.pop(session_id, None)
    with _locks_guard:
        _session_locks.pop(session_id, None)
