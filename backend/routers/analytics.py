from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from auth import AuthenticatedUser, get_current_user
from models import AnalyticsEventRequest
from services import llm_cache, token_meter
from services.persistence import persist_analytics_event
from services.rate_limit import check_rate_limit
from services.supabase_client import get_supabase

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/event", status_code=202)
async def track_event(
    req: AnalyticsEventRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
):
    check_rate_limit(user.id, max_per_minute=60)
    background_tasks.add_task(
        persist_analytics_event, user.id, req.session_id, req.event, req.properties,
    )
    return {"status": "accepted"}


@router.get("/llm-cache")
async def get_llm_cache_stats(user: AuthenticatedUser = Depends(get_current_user)):
    """Operational counters for the LLM response cache (services.llm_cache).

    Aggregate only — no prompts, responses, or per-user data. The
    est_*_tokens_saved figures are measured against the actual prompt and
    response sizes each cached entry stands in for, so they're the input to
    the model cost comparison rather than a guess. In-process counters, so
    they reset on restart and are per-replica."""
    return llm_cache.stats()


@router.get("/llm-usage")
async def get_llm_usage(user: AuthenticatedUser = Depends(get_current_user)):
    """Provider-reported token usage broken down by call site.

    Token counts are exact (they come from the provider's own `usage` object,
    not an estimate). The dollar figures multiply those counts by
    token_meter.PRICING, which is indicative rather than vendor-verified —
    see the caveat on that constant before using this for budgeting.

    In-process counters: they reset on restart and are per-replica."""
    return token_meter.stats()


@router.get("/stats")
async def get_stats(user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)
    sb = get_supabase()
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        sessions = sb.table("sessions").select(
            "id,track,status,overall_score,created_at,ended_at"
        ).eq("user_id", user.id).execute().data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"sessions query failed: {exc}") from exc

    try:
        events = sb.table("analytics_events").select(
            "event,properties,created_at"
        ).eq("user_id", user.id).execute().data or []
    except Exception:
        events = []

    completed = [s for s in sessions if s.get("status") == "completed"]
    scores = [s["overall_score"] for s in completed if s.get("overall_score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    tracks = ["behavioral", "technical", "system-design"]
    sessions_by_track = {}
    avg_score_by_track = {}
    completion_by_track = {}
    for track in tracks:
        ts = [s for s in sessions if s.get("track") == track]
        tc = [s for s in ts if s.get("status") == "completed"]
        ts_scores = [s["overall_score"] for s in tc if s.get("overall_score") is not None]
        sessions_by_track[track] = len(ts)
        avg_score_by_track[track] = round(sum(ts_scores) / len(ts_scores), 1) if ts_scores else 0
        completion_by_track[track] = round(len(tc) / len(ts) * 100) if ts else 0

    now_utc = datetime.now(timezone.utc)
    daily: dict[str, int] = {}
    for i in range(13, -1, -1):
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
        "sessions_last_14_days": daily,
        "language_usage": lang_counts,
        "score_distribution": buckets,
    }
