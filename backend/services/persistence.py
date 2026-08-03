"""
All Supabase write operations for interview sessions.
Each function is a fire-and-ignore: if Supabase is not configured (local dev),
or a write fails, the in-memory session remains authoritative and we continue.
"""

from __future__ import annotations

from services.logger import log
from services.session_store import now
from services.supabase_client import get_supabase


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
        "created_at": now(),
    }).execute()
    sb.table("messages").insert({
        "session_id": session_id,
        "role": "interviewer",
        "content": question,
        "sequence_no": 0,
        "created_at": now(),
    }).execute()


def persist_assigned_question(session_id: str, question_id: str) -> None:
    sb = get_supabase()
    if not sb:
        return
    sb.table("sessions").update({"assigned_question_id": question_id}).eq("id", session_id).execute()


def persist_diagram(session_id: str, elements: list[dict]) -> None:
    """Autosaved periodically while the candidate draws (see /interview/diagram),
    so a refresh/resume mid system-design session restores the board, not just
    the conversation."""
    sb = get_supabase()
    if not sb:
        return
    sb.table("sessions").update({"diagram_elements": elements}).eq("id", session_id).execute()


def persist_message(session_id: str, role: str, content: str, sequence_no: int) -> None:
    sb = get_supabase()
    if not sb:
        return
    sb.table("messages").insert({
        "session_id": session_id,
        "role": role,
        "content": content,
        "sequence_no": sequence_no,
        "created_at": now(),
    }).execute()


def persist_evaluation(session_id: str, result: dict) -> None:
    sb = get_supabase()
    if not sb:
        return
    star = result.get("star_analysis")
    diagram = result.get("diagram_evaluation")
    update_payload = {
        "status": "completed",
        "overall_score": result.get("overall_score"),
        "summary": result.get("summary"),
        "star_analysis": star if star else None,
        "ended_at": now(),
    }
    if diagram is not None:
        update_payload["diagram_evaluation"] = diagram
    sb.table("sessions").update(update_payload).eq("id", session_id).execute()
    for category in result.get("evaluations", []):
        sb.table("evaluations").insert({
            "session_id": session_id,
            "category": category.get("category"),
            "score": category.get("score"),
            "feedback": category.get("feedback"),
            "topic": category.get("topic"),
        }).execute()


def persist_analytics_event(user_id: str, session_id: str | None, event: str, properties: dict | None) -> None:
    """Best-effort usage/click tracking. Analytics must never break the
    feature it's observing, so unlike the other persist_* functions here,
    failures are caught and logged rather than left to propagate."""
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("analytics_events").insert({
            "user_id": user_id,
            "session_id": session_id,
            "event": event,
            "properties": properties or {},
            "created_at": now(),
        }).execute()
    except Exception as exc:
        log.warning("analytics.persist_failed", tracked_event=event, error=str(exc))
