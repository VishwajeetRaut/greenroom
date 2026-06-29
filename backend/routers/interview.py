import asyncio
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from auth import AuthenticatedUser, get_current_user
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
from services import llm, piston, question_bank, question_generator, harness_generator
from services import test_runner
from services.rate_limit import check_rate_limit
from services.supabase_client import get_supabase

router = APIRouter(prefix="/interview", tags=["interview"])

# Process-local cache only — Supabase is the source of truth (see _get_session).
# This means the backend is stateless across restarts/replicas: if a request
# lands on a replica that never saw this session, it gets rebuilt from the DB
# instead of 404ing. Required because the backend is deployed with up to 2
# replicas (DEPLOYMENT.md) and Azure's load balancer does not guarantee a
# session sticks to the replica that created it.
SESSIONS: dict[str, dict] = {}
# asyncio.Lock, not threading.Lock — these handlers are async, and a blocking
# lock would freeze the whole event loop (not just one request) if two calls
# for the same session land concurrently. _locks_guard only protects the brief,
# non-blocking dict lookup/insert below, never held across an await.
_session_locks: dict[str, asyncio.Lock] = {}
_locks_guard = threading.Lock()


def _session_lock(session_id: str) -> asyncio.Lock:
    with _locks_guard:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        return _session_locks[session_id]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_session(session_id: str) -> dict | None:
    """Returns the session, rebuilding it from Supabase into the local cache
    if this replica doesn't have it in memory. Returns None only if the
    session truly doesn't exist (or isn't persisted, e.g. Supabase not
    configured and this replica never created it)."""
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

    session = {
        "track": row["track"],
        "role": row.get("role") or "Software Engineer",
        "history": history,
        "user_id": row.get("user_id"),
        "assigned_question": assigned_question,
        "next_sequence_no": len(history),
    }
    SESSIONS[session_id] = session
    return session


def _check_ownership(session: dict, user: AuthenticatedUser) -> None:
    owner = session.get("user_id")
    if owner and owner != user.id:
        raise HTTPException(status_code=403, detail="You don't have access to this session")


def _persist_assigned_question(session_id: str, question_id: str):
    sb = get_supabase()
    if not sb:
        return
    sb.table("sessions").update({"assigned_question_id": question_id}).eq("id", session_id).execute()


def _persist_session_start(session_id: str, user_id: str, track: str, role: str, question: str, assigned_question_id: str | None):
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
            "assigned_question_id": assigned_question_id,
            "created_at": _now(),
        }
    ).execute()
    sb.table("messages").insert(
        {"session_id": session_id, "role": "interviewer", "content": question, "sequence_no": 0, "created_at": _now()}
    ).execute()


def _persist_message(session_id: str, role: str, content: str, sequence_no: int):
    sb = get_supabase()
    if not sb:
        return
    sb.table("messages").insert(
        {"session_id": session_id, "role": role, "content": content, "sequence_no": sequence_no, "created_at": _now()}
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
async def start_session(req: StartSessionRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)

    session_id = str(uuid.uuid4())
    # Question selection happens lazily on the candidate's first reply (see
    # post_message) instead of here — at this point they haven't said anything
    # yet, so the interviewer LLM would have zero real signal to choose from
    # and ends up defaulting to the same "obvious" pick every session.
    greeting = await run_in_threadpool(llm.opening_message, req.track, req.role)

    SESSIONS[session_id] = {
        "track": req.track,
        "role": req.role,
        "history": [{"role": "interviewer", "content": greeting}],
        "user_id": user.id,
        "assigned_question": None,
        "next_sequence_no": 1,
    }

    await run_in_threadpool(
        _persist_session_start, session_id, user.id, req.track, req.role, greeting,
        assigned_question_id=None,
    )

    return StartSessionResponse(session_id=session_id, track=req.track, question=greeting)


@router.post("/message", response_model=MessageResponse)
async def post_message(req: MessageRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)

    async with _session_lock(req.session_id):
        session = _get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        _check_ownership(session, user)

        candidate_content = req.message
        if req.code:
            candidate_content += f"\n\n[Candidate's current code]\n{req.code}"

        is_first_reply = session["track"] == "technical" and session.get("assigned_question") is None
        session["history"].append({"role": "candidate", "content": candidate_content})
        await run_in_threadpool(_persist_message, req.session_id, "candidate", req.message, session["next_sequence_no"])
        session["next_sequence_no"] += 1

        if is_first_reply:
            # Now the LLM has real context (the candidate's own words) to choose
            # a genuinely well-matched problem instead of a generic default.
            session["assigned_question"] = await question_generator.select_or_generate_question(
                session["role"], candidate_intro=req.message,
            )
            if session["assigned_question"]:
                await run_in_threadpool(_persist_assigned_question, req.session_id, session["assigned_question"]["id"])

        question = await run_in_threadpool(
            llm.next_question, session["track"], session["role"], session["history"],
            session.get("assigned_question"),
        )

        session["history"].append({"role": "interviewer", "content": question})
        await run_in_threadpool(_persist_message, req.session_id, "interviewer", question, session["next_sequence_no"])
        session["next_sequence_no"] += 1

    return MessageResponse(question=question)


@router.post("/code/run")
async def run_code(req: RunCodeRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id, max_per_minute=20)
    result = await piston.run_code(req.language, req.version, req.source, req.stdin or "")
    return result


@router.post("/code/test", response_model=RunTestsResponse)
async def run_tests(req: RunTestsRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id, max_per_minute=20)

    session = _get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_ownership(session, user)

    assigned = session.get("assigned_question")
    is_stdio = bool(assigned and assigned.get("tests") and "stdin" in assigned["tests"][0])
    if is_stdio:
        # Bank data (imported from CodeContests) stores the C++ entry as "cpp";
        # the frontend sends Piston/Wandbox's actual compiler id "gcc" for C++.
        # Accept either spelling rather than relying on the bank data matching
        # the wire format exactly.
        allowed = set(assigned.get("languages") or [])
        if "cpp" in allowed:
            allowed.add("gcc")
        if req.language not in allowed:
            return RunTestsResponse(
                status="compile_error",
                compile_error=f"This problem doesn't support {req.language} yet.",
                error_type="permanent",
                visible_tests=[], hidden_tests=[], passed=0, total=0,
            )
        parsed = await test_runner.run_stdio_tests(
            req.language, req.version, req.source,
            assigned["tests"], assigned.get("visible_count", 3),
        )
        return RunTestsResponse(**parsed)

    # Java/C++ aren't supported by the call/expected harness format below — the
    # 210 imported LeetCode problems only ever had verified Python test cases.
    # Generate (and cache) a real harness for this problem on first use instead
    # of refusing the language outright.
    bank_lang = "cpp" if req.language == "gcc" else req.language
    if assigned and bank_lang in ("java", "cpp") and bank_lang not in (assigned.get("languages") or []):
        harness_data = await harness_generator.get_or_generate(assigned, bank_lang)
        if harness_data:
            if bank_lang == "java":
                full_source = harness_generator.merge_java_sources(req.source, harness_data["harness"])
            else:
                full_source = req.source + "\n\n" + harness_data["harness"]
            result = await piston.run_code(req.language, req.version, full_source, stdin="")
            raw = result.get("run", {})
            parsed = test_runner.parse_results(raw.get("stdout", ""), raw.get("stderr", ""))
            return RunTestsResponse(**parsed)
        return RunTestsResponse(
            status="compile_error",
            compile_error=f"This problem doesn't support {req.language} yet.",
            error_type="permanent",
            visible_tests=[], hidden_tests=[], passed=0, total=0,
        )

    harness = test_runner.generate_harness(
        req.language, req.source, session["history"],
        assigned_question=session.get("assigned_question"),
    )
    if harness is None:
        lang = req.language
        if lang not in ("python", "node"):
            msg = f"Test cases are not yet supported for {lang}. Switch to Python or JavaScript to use the test runner."
        else:
            msg = "No coding problem has been assigned yet — wait for the interviewer to give you a problem first."
        return RunTestsResponse(
            status="compile_error",
            compile_error=msg,
            error_type="permanent",
            visible_tests=[],
            hidden_tests=[],
            passed=0,
            total=0,
        )

    result = await piston.run_code(req.language, req.version, harness)
    raw = result.get("run", {})
    parsed = test_runner.parse_results(raw.get("stdout", ""), raw.get("stderr", ""))
    return RunTestsResponse(**parsed)


@router.delete("/{session_id}")
def delete_session(session_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    session = _get_session(session_id)
    if session:
        _check_ownership(session, user)

    SESSIONS.pop(session_id, None)
    with _locks_guard:
        _session_locks.pop(session_id, None)

    sb = get_supabase()
    if sb:
        sb.table("evaluations").delete().eq("session_id", session_id).execute()
        sb.table("messages").delete().eq("session_id", session_id).execute()
        sb.table("sessions").delete().eq("id", session_id).execute()
    return {"deleted": session_id}


@router.post("/end", response_model=EndSessionResponse)
async def end_session(req: EndSessionRequest, user: AuthenticatedUser = Depends(get_current_user)):
    async with _session_lock(req.session_id):
        session = _get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        _check_ownership(session, user)

        has_candidate_answer = any(t["role"] == "candidate" for t in session["history"])
        if not has_candidate_answer:
            empty_result = {
                "overall_score": 0,
                "summary": "No answers were recorded in this session. Start a new session and answer at least one question to receive a score.",
                "evaluations": [],
            }
            await run_in_threadpool(_persist_evaluation, req.session_id, empty_result)
            return EndSessionResponse(
                overall_score=0,
                summary=empty_result["summary"],
                evaluations=[],
            )

        result = await run_in_threadpool(llm.evaluate_session, session["track"], session["role"], session["history"])
        await run_in_threadpool(_persist_evaluation, req.session_id, result)

    return EndSessionResponse(
        overall_score=result.get("overall_score", 5),
        summary=result.get("summary", ""),
        star_analysis=result.get("star_analysis"),
        evaluations=result.get("evaluations", []),
    )
