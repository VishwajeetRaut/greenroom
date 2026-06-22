import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from models import (
    StartSessionRequest,
    StartSessionResponse,
    MessageRequest,
    MessageResponse,
    RunCodeRequest,
    RunTestsRequest,
    RunTestsResponse,
    EndSessionRequest,
    EndSessionResponse,
)
from services import llm, piston
from services import test_runner
from services.supabase_client import get_supabase

router = APIRouter(prefix="/interview", tags=["interview"])

# In-memory session store. Good enough for a single backend instance / demo deployments.
# For multi-instance deployments, back this with Redis or read/write history from Supabase.
SESSIONS: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_session_start(session_id: str, user_id: str | None, track: str, role: str, question: str):
    sb = get_supabase()
    if not sb:
        return
    sb.table("sessions").insert(
        {
            "id": session_id,
            "user_id": user_id,
            "track": track,
            "role": role,
            "status": "active",
            "created_at": _now(),
        }
    ).execute()
    sb.table("messages").insert(
        {"session_id": session_id, "role": "interviewer", "content": question, "created_at": _now()}
    ).execute()


def _persist_message(session_id: str, role: str, content: str):
    sb = get_supabase()
    if not sb:
        return
    sb.table("messages").insert(
        {"session_id": session_id, "role": role, "content": content, "created_at": _now()}
    ).execute()


def _persist_evaluation(session_id: str, result: dict):
    sb = get_supabase()
    if not sb:
        return

    star = result.get("star_analysis")
    sb.table("sessions").update(
        {
            "status": "completed",
            "overall_score": result.get("overall_score"),
            "summary": result.get("summary"),
            "star_analysis": star if star else None,
            "ended_at": _now(),
        }
    ).eq("id", session_id).execute()

    for category in result.get("evaluations", []):
        sb.table("evaluations").insert(
            {
                "session_id": session_id,
                "category": category.get("category"),
                "score": category.get("score"),
                "feedback": category.get("feedback"),
            }
        ).execute()


@router.post("/start", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest):
    session_id = str(uuid.uuid4())
    question = llm.opening_question(req.track)

    SESSIONS[session_id] = {
        "track": req.track,
        "role": req.role,
        "history": [{"role": "interviewer", "content": question}],
    }

    _persist_session_start(session_id, req.user_id, req.track, req.role, question)

    return StartSessionResponse(session_id=session_id, track=req.track, question=question)


@router.post("/message", response_model=MessageResponse)
def post_message(req: MessageRequest):
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    candidate_content = req.message
    if req.code:
        candidate_content += f"\n\n[Candidate's current code]\n{req.code}"

    session["history"].append({"role": "candidate", "content": candidate_content})
    _persist_message(req.session_id, "candidate", req.message)

    question = llm.next_question(session["track"], session["role"], session["history"])

    session["history"].append({"role": "interviewer", "content": question})
    _persist_message(req.session_id, "interviewer", question)

    return MessageResponse(question=question)


@router.post("/code/run")
async def run_code(req: RunCodeRequest):
    result = await piston.run_code(req.language, req.version, req.source, req.stdin or "")
    return result


@router.post("/code/test", response_model=RunTestsResponse)
async def run_tests(req: RunTestsRequest):
    harness = test_runner.build_harness(req.language, req.source)
    if harness is None:
        # Language not supported by harness — run raw and wrap output
        result = await piston.run_code(req.language, req.version, req.source)
        raw = result.get("run", {})
        return RunTestsResponse(
            status="compile_error",
            compile_error=raw.get("stderr") or raw.get("stdout") or "Language not supported for test cases.",
            visible_tests=[],
            hidden_tests=[],
            passed=0,
            total=7,
        )

    result = await piston.run_code(req.language, req.version, harness)
    raw = result.get("run", {})
    parsed = test_runner.parse_results(raw.get("stdout", ""), raw.get("stderr", ""))
    return RunTestsResponse(**parsed)


@router.delete("/{session_id}")
def delete_session(session_id: str):
    SESSIONS.pop(session_id, None)
    sb = get_supabase()
    if sb:
        sb.table("evaluations").delete().eq("session_id", session_id).execute()
        sb.table("messages").delete().eq("session_id", session_id).execute()
        sb.table("sessions").delete().eq("id", session_id).execute()
    return {"deleted": session_id}


@router.post("/end", response_model=EndSessionResponse)
def end_session(req: EndSessionRequest):
    session = SESSIONS.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    has_candidate_answer = any(t["role"] == "candidate" for t in session["history"])
    if not has_candidate_answer:
        empty_result = {
            "overall_score": 0,
            "summary": "No answers were recorded in this session. Start a new session and answer at least one question to receive a score.",
            "evaluations": [],
        }
        _persist_evaluation(req.session_id, empty_result)
        return EndSessionResponse(
            overall_score=0,
            summary=empty_result["summary"],
            evaluations=[],
        )

    result = llm.evaluate_session(session["track"], session["role"], session["history"])
    _persist_evaluation(req.session_id, result)

    return EndSessionResponse(
        overall_score=result.get("overall_score", 5),
        summary=result.get("summary", ""),
        star_analysis=result.get("star_analysis"),
        evaluations=result.get("evaluations", []),
    )
