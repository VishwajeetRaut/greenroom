"""
All Supabase writes for interview sessions. Functions here are fire-and-forget
safe: if Supabase is unreachable the session continues in memory and the write
is silently skipped (rather than crashing the candidate's session mid-flow).
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.supabase_client import get_supabase


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_session_start(
    session_id: str, user_id: str, track: str, role: str,
    question: str, assigned_question_id: str | None,
) -> None:
    sb = get_supabase()
    if not sb:
        return
    sb.table("sessions").insert({
        "id": session_id,
        "user_id": user_id,
        "track": track,
        "role": role,
        "status": "active",
        "assigned_question_id": assigned_question_id,
        "created_at": _now(),
    }).execute()
    sb.table("messages").insert({
        "session_id": session_id,
        "role": "interviewer",
        "content": question,
        "sequence_no": 0,
        "created_at": _now(),
    }).execute()


def persist_message(session_id: str, role: str, content: str, sequence_no: int) -> None:
    sb = get_supabase()
    if not sb:
        return
    sb.table("messages").insert({
        "session_id": session_id,
        "role": role,
        "content": content,
        "sequence_no": sequence_no,
        "created_at": _now(),
    }).execute()


def persist_assigned_question(session_id: str, question_id: str) -> None:
    sb = get_supabase()
    if not sb:
        return
    sb.table("sessions").update({"assigned_question_id": question_id}).eq("id", session_id).execute()


def persist_evaluation(session_id: str, result: dict) -> None:
    sb = get_supabase()
    if not sb:
        return
    star = result.get("star_analysis")
    sb.table("sessions").update({
        "status": "completed",
        "overall_score": result.get("overall_score"),
        "summary": result.get("summary"),
        "star_analysis": star or None,
        "ended_at": _now(),
    }).eq("id", session_id).execute()

    for category in result.get("evaluations", []):
        sb.table("evaluations").insert({
            "session_id": session_id,
            "category": category.get("category"),
            "score": category.get("score"),
            "feedback": category.get("feedback"),
        }).execute()
