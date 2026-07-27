"""Router-level tests for GET /analytics/stats — the days/track filters.

Mounts only the analytics router in a bare FastAPI app (not the full `main`
app, which loads real Supabase creds from .env at import time) and patches
get_supabase so no test can touch a real database. A single MagicMock
routes to a different child mock per table name so the "sessions" and
"analytics_events" queries can be configured independently.
"""
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import AuthenticatedUser, get_current_user
from routers import analytics


def _client():
    app = FastAPI()
    app.include_router(analytics.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id="user-1")
    return TestClient(app)


def _sb_with(sessions_data, events_data=None):
    """Supports both the unfiltered chain (`.eq("user_id").execute()`) and
    the track-filtered chains (`.eq("user_id").eq("track").execute()` for
    sessions, `.eq("user_id").in_("session_id", ...).execute()` for events)."""
    sessions_mock = MagicMock()
    sessions_mock.select.return_value.eq.return_value.execute.return_value.data = sessions_data
    sessions_mock.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = sessions_data

    events_mock = MagicMock()
    events_mock.select.return_value.eq.return_value.execute.return_value.data = events_data or []
    events_mock.select.return_value.eq.return_value.in_.return_value.execute.return_value.data = events_data or []

    sb = MagicMock()
    sb.table.side_effect = lambda name: sessions_mock if name == "sessions" else events_mock
    return sb


def test_get_stats_503_when_supabase_unconfigured():
    with patch.object(analytics, "get_supabase", return_value=None):
        resp = _client().get("/api/analytics/stats")
    assert resp.status_code == 503


def test_get_stats_default_days_window_is_14():
    with patch.object(analytics, "get_supabase", return_value=_sb_with([])):
        resp = _client().get("/api/analytics/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 14
    assert len(body["sessions_by_day"]) == 14
    assert body["track_filter"] is None


def test_get_stats_custom_days_window():
    with patch.object(analytics, "get_supabase", return_value=_sb_with([])):
        resp = _client().get("/api/analytics/stats?days=7")
    body = resp.json()
    assert body["days"] == 7
    assert len(body["sessions_by_day"]) == 7


def test_get_stats_rejects_days_out_of_range():
    assert _client().get("/api/analytics/stats?days=0").status_code == 422
    assert _client().get("/api/analytics/stats?days=91").status_code == 422


def test_get_stats_rejects_invalid_track():
    resp = _client().get("/api/analytics/stats?track=not-a-track")
    assert resp.status_code == 422


def test_get_stats_track_filter_scopes_sessions():
    filtered_sessions = [
        {"id": "s-1", "track": "technical", "status": "completed", "overall_score": 8,
         "created_at": "2026-07-01T00:00:00+00:00", "ended_at": None},
    ]
    with patch.object(analytics, "get_supabase", return_value=_sb_with(filtered_sessions)):
        resp = _client().get("/api/analytics/stats?track=technical")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_sessions"] == 1
    assert body["sessions_by_track"]["technical"] == 1
    assert body["sessions_by_track"]["behavioral"] == 0
    assert body["track_filter"] == "technical"


def test_get_stats_no_track_filter_returns_all_sessions():
    sessions = [
        {"id": "s-1", "track": "technical", "status": "completed", "overall_score": 8,
         "created_at": "2026-07-01T00:00:00+00:00", "ended_at": None},
        {"id": "s-2", "track": "behavioral", "status": "active", "overall_score": None,
         "created_at": "2026-07-02T00:00:00+00:00", "ended_at": None},
    ]
    with patch.object(analytics, "get_supabase", return_value=_sb_with(sessions)):
        resp = _client().get("/api/analytics/stats")
    body = resp.json()
    assert body["total_sessions"] == 2
    assert body["track_filter"] is None


def test_get_stats_language_usage_from_code_run_events():
    events = [{"event": "code_run", "properties": {"language": "python"}, "created_at": "2026-07-01T00:00:00+00:00"}]
    with patch.object(analytics, "get_supabase", return_value=_sb_with([], events)):
        resp = _client().get("/api/analytics/stats")
    assert resp.json()["language_usage"] == {"python": 1}
