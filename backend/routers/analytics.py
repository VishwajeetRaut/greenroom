from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from auth import AuthenticatedUser, get_current_user
from models import AnalyticsEventRequest
from services.persistence import persist_analytics_event
from services.supabase_client import get_supabase

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/event", status_code=202)
async def track_event(
    req: AnalyticsEventRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
):
    background_tasks.add_task(
        persist_analytics_event, user.id, req.session_id, req.event, req.properties,
    )
    return {"status": "accepted"}


@router.get("/stats")
async def get_stats(
    days: int = Query(default=14, ge=1, le=90),
    track: Optional[Literal["behavioral", "technical", "system-design"]] = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    sb = get_supabase()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        query = sb.table("sessions").select(
            "id,track,status,overall_score,created_at,ended_at"
        ).eq("user_id", user.id)
        if track:
            query = query.eq("track", track)
        sessions = query.execute().data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"sessions query failed: {exc}") from exc

    try:
        events_query = sb.table("analytics_events").select(
            "event,properties,created_at"
        ).eq("user_id", user.id)
        if track:
            session_ids = [s["id"] for s in sessions]
            events = events_query.in_("session_id", session_ids).execute().data if session_ids else []
        else:
            events = events_query.execute().data or []
    except Exception:
        events = []

    completed = [s for s in sessions if s.get("status") == "completed"]
    scores = [s["overall_score"] for s in completed if s.get("overall_score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    tracks = ["behavioral", "technical", "system-design"]
    sessions_by_track = {}
    avg_score_by_track = {}
    completion_by_track = {}
    for t in tracks:
        ts = [s for s in sessions if s.get("track") == t]
        tc = [s for s in ts if s.get("status") == "completed"]
        ts_scores = [s["overall_score"] for s in tc if s.get("overall_score") is not None]
        sessions_by_track[t] = len(ts)
        avg_score_by_track[t] = round(sum(ts_scores) / len(ts_scores), 1) if ts_scores else 0
        completion_by_track[t] = round(len(tc) / len(ts) * 100) if ts else 0

    now_utc = datetime.now(timezone.utc)
    daily: dict[str, int] = {}
    for i in range(days - 1, -1, -1):
        day = (now_utc - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[day] = 0
    for s in sessions:
        if s.get("created_at"):
            day = s["created_at"][:10]
            if day in daily:
                daily[day] += 1

    lang_counts: dict[str, int] = {}
    for e in events:
        if e.get("event") == "code_run" and e.get("properties"):
            lang = e["properties"].get("language", "unknown")
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    # Score distribution buckets: 0-2, 3-4, 5-6, 7-8, 9-10
    buckets = {"0–2": 0, "3–4": 0, "5–6": 0, "7–8": 0, "9–10": 0}
    for sc in scores:
        if sc <= 2:
            buckets["0–2"] += 1
        elif sc <= 4:
            buckets["3–4"] += 1
        elif sc <= 6:
            buckets["5–6"] += 1
        elif sc <= 8:
            buckets["7–8"] += 1
        else:
            buckets["9–10"] += 1

    return {
        "total_sessions": len(sessions),
        "completed_sessions": len(completed),
        "avg_score": avg_score,
        "sessions_by_track": sessions_by_track,
        "avg_score_by_track": avg_score_by_track,
        "completion_rate_by_track": completion_by_track,
        "sessions_by_day": daily,
        "days": days,
        "track_filter": track,
        "language_usage": lang_counts,
        "score_distribution": buckets,
    }
