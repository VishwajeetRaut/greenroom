"""Router-level tests for routers/interview.py — the biggest, most complex
router in the app (session start/message/resume/diagram/code-test/delete/end)
with no prior direct test coverage.

These tests mount only the interview router (not the full `main` app, which
calls load_dotenv() at import time and would pick up real Supabase creds from
.env) and patch every get_supabase reference the request path can reach so no
test can ever touch a real database.
"""
import uuid
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import AuthenticatedUser, get_current_user
from routers import interview
from services import persistence, rate_limit, session_guard, session_store, supabase_client


@pytest.fixture(autouse=True)
def _no_real_supabase_and_clean_state():
    """Every get_supabase binding the interview request path can reach is
    forced to None (the documented "Supabase unconfigured" fallback), and
    per-test state (in-memory sessions, rate-limit buckets) is reset."""
    with ExitStack() as stack:
        for module in (interview, session_guard, persistence, session_store, supabase_client):
            stack.enter_context(patch.object(module, "get_supabase", return_value=None))
        session_store.SESSIONS.clear()
        rate_limit._buckets.clear()
        yield


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(interview.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id="user-1")
    return TestClient(app)


def _seed_session(session_id: str, **overrides) -> dict:
    session = {
        "track": "behavioral",
        "role": "Software Engineer",
        "history": [{"role": "interviewer", "content": "Tell me about yourself."}],
        "user_id": "user-1",
        "assigned_question": None,
        "next_sequence_no": 1,
        "last_activity_at": session_store.now(),
        "job_description": None,
        "status": "active",
        "diagram_elements": [],
        "asked_question_ids": set(),
        "candidate_intro": "",
    }
    session.update(overrides)
    session_store.SESSIONS[session_id] = session
    return session


# --- POST /interview/start ------------------------------------------------

def test_start_session_success(client):
    with patch.object(interview.llm, "opening_message", return_value="Tell me about a challenge you faced."):
        resp = client.post("/api/interview/start", json={"track": "behavioral"})
    assert resp.status_code == 200
    body = resp.json()
    assert uuid.UUID(body["session_id"])  # a real UUID was generated
    assert body["track"] == "behavioral"
    assert body["question"] == "Tell me about a challenge you faced."
    assert body["session_id"] in session_store.SESSIONS


def test_start_session_rejects_invalid_track(client):
    resp = client.post("/api/interview/start", json={"track": "not-a-real-track"})
    assert resp.status_code == 422


# --- POST /interview/message ------------------------------------------------

def test_message_session_not_found(client):
    resp = client.post("/api/interview/message", json={
        "session_id": str(uuid.uuid4()), "message": "hi",
    })
    assert resp.status_code == 404


def test_message_ownership_enforced(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, user_id="someone-else")
    resp = client.post("/api/interview/message", json={"session_id": session_id, "message": "hi"})
    assert resp.status_code == 403


def test_message_turn_limit_reached_short_circuits(client):
    session_id = str(uuid.uuid4())
    turns = [{"role": "candidate", "content": "answer"}] * interview.is_turn_limit_reached.__globals__["MAX_CANDIDATE_TURNS"]
    _seed_session(session_id, history=turns)
    resp = client.post("/api/interview/message", json={"session_id": session_id, "message": "one more"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is True
    assert "End session" in body["question"]


def test_message_happy_path_behavioral_follow_up(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, assigned_question={"id": "q-1"})
    with patch.object(interview.llm, "next_question", return_value="Tell me more about that."):
        resp = client.post("/api/interview/message", json={"session_id": session_id, "message": "I led a project."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "Tell me more about that."
    assert body["done"] is False


# --- GET /interview/{session_id}/resume ------------------------------------------------

def test_resume_session_not_found(client):
    resp = client.get(f"/api/interview/{uuid.uuid4()}/resume")
    assert resp.status_code == 404


def test_resume_session_rejects_completed_session(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, status="completed")
    resp = client.get(f"/api/interview/{session_id}/resume")
    assert resp.status_code == 409


def test_resume_session_returns_history_and_bumps_activity(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, last_activity_at="2020-01-01T00:00:00+00:00")
    resp = client.get(f"/api/interview/{session_id}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["track"] == "behavioral"
    assert len(body["history"]) == 1
    assert session_store.SESSIONS[session_id]["last_activity_at"] != "2020-01-01T00:00:00+00:00"


# --- POST /interview/diagram ------------------------------------------------

def test_save_diagram_session_not_found(client):
    resp = client.post("/api/interview/diagram", json={"session_id": str(uuid.uuid4()), "elements": []})
    assert resp.status_code == 404


def test_save_diagram_ownership_enforced(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, user_id="someone-else")
    resp = client.post("/api/interview/diagram", json={"session_id": session_id, "elements": []})
    assert resp.status_code == 403


def test_save_diagram_success(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id)
    elements = [{"type": "box", "x": 1}]
    resp = client.post("/api/interview/diagram", json={"session_id": session_id, "elements": elements})
    assert resp.status_code == 200
    assert resp.json() == {"saved": True}
    assert session_store.SESSIONS[session_id]["diagram_elements"] == elements


# --- DELETE /interview/{session_id} ------------------------------------------------

def test_delete_session_503_when_supabase_unconfigured(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id)
    resp = client.delete(f"/api/interview/{session_id}")
    assert resp.status_code == 503
    # Eviction from the in-memory cache still happens before the 503 is raised.
    assert session_id not in session_store.SESSIONS


def test_delete_session_success_when_supabase_configured(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id)
    sb = MagicMock()
    with patch.object(interview, "get_supabase", return_value=sb):
        resp = client.delete(f"/api/interview/{session_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": session_id}
    deleted_tables = {c.args[0] for c in sb.table.call_args_list}
    # analytics_events is deleted too — a deleted session must not leave
    # orphaned per-session analytics rows behind. The endpoint was extended to
    # cover it; this assertion wasn't updated with it.
    assert deleted_tables == {"evaluations", "messages", "analytics_events", "sessions"}


def test_delete_session_ownership_enforced(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, user_id="someone-else")
    sb = MagicMock()
    with patch.object(interview, "get_supabase", return_value=sb):
        resp = client.delete(f"/api/interview/{session_id}")
    assert resp.status_code == 403


# --- POST /interview/end ------------------------------------------------

def test_end_session_not_found(client):
    resp = client.post("/api/interview/end", json={"session_id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_end_session_with_no_candidate_answers_scores_zero(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, history=[{"role": "interviewer", "content": "Hi"}])
    resp = client.post("/api/interview/end", json={"session_id": session_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_score"] == 0
    assert body["evaluations"] == []
    assert session_store.SESSIONS[session_id]["status"] == "completed"


def test_end_session_happy_path(client):
    session_id = str(uuid.uuid4())
    _seed_session(session_id, history=[
        {"role": "interviewer", "content": "Tell me about yourself."},
        {"role": "candidate", "content": "I'm an engineer."},
    ])
    eval_result = {
        "overall_score": 8,
        "summary": "Strong answer.",
        "evaluations": [{"category": "communication", "score": 8, "feedback": "clear"}],
    }
    with patch.object(interview.llm, "evaluate_session", return_value=eval_result):
        resp = client.post("/api/interview/end", json={"session_id": session_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_score"] == 8
    assert body["summary"] == "Strong answer."
    assert session_store.SESSIONS[session_id]["status"] == "completed"
