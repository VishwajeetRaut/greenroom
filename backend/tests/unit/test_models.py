"""Smoke tests: Pydantic models parse and reject correctly."""
import pytest
from pydantic import ValidationError

from models import (
    AnalyticsEventRequest,
    EndSessionRequest,
    MessageRequest,
    RunTestsRequest,
    SaveDiagramRequest,
    StartSessionRequest,
)


def test_start_session_valid():
    r = StartSessionRequest(track="technical", role="Software Engineer")
    assert r.track == "technical"


def test_start_session_defaults():
    r = StartSessionRequest(track="behavioral")
    assert r.role == "Software Engineer"


def test_message_request_requires_session_and_message():
    with pytest.raises(ValidationError):
        MessageRequest()  # missing required fields


# --- session_id must be a well-formed UUID -----------------------------
# Session ids are always generated server-side via uuid.uuid4(), so any
# request carrying a malformed one is rejected at the validation boundary
# rather than reaching Supabase.

VALID_UUID = "5b1b1b1b-1b1b-4b1b-8b1b-1b1b1b1b1b1b"


@pytest.mark.parametrize("model,kwargs", [
    (MessageRequest, {"message": "hi"}),
    (RunTestsRequest, {"language": "python", "version": "3", "source": "x"}),
    (SaveDiagramRequest, {}),
    (EndSessionRequest, {}),
])
def test_session_id_rejects_malformed_uuid(model, kwargs):
    with pytest.raises(ValidationError):
        model(session_id="not-a-uuid", **kwargs)


@pytest.mark.parametrize("model,kwargs", [
    (MessageRequest, {"message": "hi"}),
    (RunTestsRequest, {"language": "python", "version": "3", "source": "x"}),
    (SaveDiagramRequest, {}),
    (EndSessionRequest, {}),
])
def test_session_id_accepts_valid_uuid(model, kwargs):
    r = model(session_id=VALID_UUID, **kwargs)
    assert r.session_id == VALID_UUID


def test_analytics_event_request_session_id_optional_and_validated():
    r = AnalyticsEventRequest(event="code_run")
    assert r.session_id is None

    r = AnalyticsEventRequest(event="code_run", session_id=VALID_UUID)
    assert r.session_id == VALID_UUID

    with pytest.raises(ValidationError):
        AnalyticsEventRequest(event="code_run", session_id="not-a-uuid")
