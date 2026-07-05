import uuid

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from auth import AuthenticatedUser, get_current_user
from models import (
    StartSessionRequest, StartSessionResponse,
    MessageRequest, MessageResponse,
    RunCodeRequest,
    RunTestsRequest, RunTestsResponse,
    BoilerplateResponse,
    EndSessionRequest, EndSessionResponse,
)
from services import llm, piston, question_generator, harness_generator, test_runner
from services import limits
from services.rate_limit import check_rate_limit
from services.session_store import get_session, get_lock, put_session, remove_session
from services.session_persistence import (
    persist_session_start, persist_message,
    persist_assigned_question, persist_evaluation,
)
from services.supabase_client import get_supabase

router = APIRouter(prefix="/interview", tags=["interview"])


def _check_ownership(session: dict, user: AuthenticatedUser) -> None:
    owner = session.get("user_id")
    if owner and owner != user.id:
        raise HTTPException(status_code=403, detail="You don't have access to this session")


# ── Session lifecycle ─────────────────────────────────────────────────────────

@router.post("/start", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)
    await run_in_threadpool(limits.check_session_count, user.id)

    session_id = str(uuid.uuid4())
    # Opening message first — question selection waits until the candidate's
    # first reply so the LLM has real context to pick a well-matched problem.
    greeting = await run_in_threadpool(llm.opening_message, req.track, req.role)

    put_session(session_id, {
        "track": req.track,
        "role": req.role,
        "history": [{"role": "interviewer", "content": greeting}],
        "user_id": user.id,
        "assigned_question": None,
        "next_sequence_no": 1,
    })
    await run_in_threadpool(
        persist_session_start, session_id, user.id, req.track, req.role, greeting, None,
    )
    return StartSessionResponse(session_id=session_id, track=req.track, question=greeting)


@router.post("/message", response_model=MessageResponse)
async def post_message(req: MessageRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)

    async with get_lock(req.session_id):
        session = get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        _check_ownership(session, user)

        if limits.is_session_full(session):
            return MessageResponse(
                question="We've had a thorough session. Click 'End session' whenever you're ready for your scored evaluation.",
                done=True,
            )

        candidate_content = req.message
        if req.code:
            candidate_content += f"\n\n[Candidate's current code]\n{req.code}"

        is_first_reply = session["track"] == "technical" and session.get("assigned_question") is None
        session["history"].append({"role": "candidate", "content": candidate_content})
        await run_in_threadpool(persist_message, req.session_id, "candidate", req.message, session["next_sequence_no"])
        session["next_sequence_no"] += 1

        if is_first_reply:
            session["assigned_question"] = await question_generator.select_or_generate_question(
                session["role"], candidate_intro=req.message,
            )
            if session["assigned_question"]:
                await run_in_threadpool(persist_assigned_question, req.session_id, session["assigned_question"]["id"])

        question = await run_in_threadpool(
            llm.next_question, session["track"], session["role"],
            session["history"], session.get("assigned_question"),
        )

        session["history"].append({"role": "interviewer", "content": question})
        await run_in_threadpool(persist_message, req.session_id, "interviewer", question, session["next_sequence_no"])
        session["next_sequence_no"] += 1

    return MessageResponse(question=question, done=limits.is_session_full(session))


@router.post("/end", response_model=EndSessionResponse)
async def end_session(req: EndSessionRequest, user: AuthenticatedUser = Depends(get_current_user)):
    async with get_lock(req.session_id):
        session = get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        _check_ownership(session, user)

        if not any(t["role"] == "candidate" for t in session["history"]):
            empty = {
                "overall_score": 0,
                "summary": "No answers were recorded. Start a new session and answer at least one question to receive a score.",
                "evaluations": [],
            }
            await run_in_threadpool(persist_evaluation, req.session_id, empty)
            return EndSessionResponse(overall_score=0, summary=empty["summary"], evaluations=[])

        result = await run_in_threadpool(llm.evaluate_session, session["track"], session["role"], session["history"])
        await run_in_threadpool(persist_evaluation, req.session_id, result)

    return EndSessionResponse(
        overall_score=result.get("overall_score", 5),
        summary=result.get("summary", ""),
        star_analysis=result.get("star_analysis"),
        evaluations=result.get("evaluations", []),
    )


@router.delete("/{session_id}")
def delete_session(session_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    session = get_session(session_id)
    if session:
        _check_ownership(session, user)
    remove_session(session_id)

    sb = get_supabase()
    if sb:
        # ON DELETE CASCADE handles messages + evaluations automatically
        sb.table("sessions").delete().eq("id", session_id).execute()
    return {"deleted": session_id}


# ── Code execution ────────────────────────────────────────────────────────────

@router.post("/code/run")
async def run_code(req: RunCodeRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id, max_per_minute=20)
    return await piston.run_code(req.language, req.version, req.source, req.stdin or "")


@router.get("/{session_id}/boilerplate", response_model=BoilerplateResponse)
async def get_boilerplate(session_id: str, language: str, user: AuthenticatedUser = Depends(get_current_user)):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_ownership(session, user)

    assigned = session.get("assigned_question")
    if not assigned:
        return BoilerplateResponse(boilerplate=None, supported=True)

    is_stdio = bool(assigned.get("tests") and "stdin" in assigned["tests"][0])
    bank_lang = "cpp" if language == "gcc" else language
    allowed = set(assigned.get("languages") or [])
    if "cpp" in allowed:
        allowed.add("gcc")

    if is_stdio:
        return BoilerplateResponse(boilerplate=None, supported=language in allowed)
    if bank_lang in (assigned.get("languages") or []):
        return BoilerplateResponse(boilerplate=None, supported=True)
    if bank_lang not in ("java", "cpp"):
        return BoilerplateResponse(boilerplate=None, supported=False)

    harness_data = await harness_generator.get_or_generate(assigned, bank_lang)
    if not harness_data:
        return BoilerplateResponse(boilerplate=None, supported=False)
    return BoilerplateResponse(boilerplate=harness_data["boilerplate"], supported=True)


@router.post("/code/test", response_model=RunTestsResponse)
async def run_tests(req: RunTestsRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id, max_per_minute=20)

    session = get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_ownership(session, user)

    assigned = session.get("assigned_question")
    is_stdio = bool(assigned and assigned.get("tests") and "stdin" in assigned["tests"][0])

    if is_stdio:
        return RunTestsResponse(**await _run_stdio(req, assigned))

    bank_lang = "cpp" if req.language == "gcc" else req.language
    if assigned and bank_lang in ("java", "cpp") and bank_lang not in (assigned.get("languages") or []):
        return RunTestsResponse(**await _run_generated_harness(req, assigned, bank_lang))

    return RunTestsResponse(**await _run_call_expected(req, session, assigned))


async def _run_stdio(req: RunTestsRequest, assigned: dict) -> dict:
    allowed = set(assigned.get("languages") or [])
    if "cpp" in allowed:
        allowed.add("gcc")
    if req.language not in allowed:
        return _unsupported_lang(req.language)
    return await test_runner.run_stdio_tests(
        req.language, req.version, req.source,
        assigned["tests"], assigned.get("visible_count", 3),
    )


async def _run_generated_harness(req: RunTestsRequest, assigned: dict, bank_lang: str) -> dict:
    harness_data = await harness_generator.get_or_generate(assigned, bank_lang)
    if not harness_data:
        return _unsupported_lang(req.language)
    if bank_lang == "java":
        full_source = harness_generator.merge_java_sources(req.source, harness_data["harness"])
    else:
        full_source = req.source + "\n\n" + harness_data["harness"]
    result = await piston.run_code(req.language, req.version, full_source, stdin="")
    raw = result.get("run", {})
    return test_runner.parse_results(raw.get("stdout", ""), raw.get("stderr", ""))


async def _run_call_expected(req: RunTestsRequest, session: dict, assigned: dict | None) -> dict:
    harness = test_runner.generate_harness(req.language, req.source, session["history"], assigned)
    if harness is None:
        msg = (
            f"Test cases are not yet supported for {req.language}. Switch to Python or JavaScript."
            if req.language not in ("python", "node")
            else "No coding problem has been assigned yet — wait for the interviewer to give you one."
        )
        return _error_response(msg, "permanent")
    result = await piston.run_code(req.language, req.version, harness)
    raw = result.get("run", {})
    return test_runner.parse_results(raw.get("stdout", ""), raw.get("stderr", ""))


def _unsupported_lang(language: str) -> dict:
    return _error_response(f"This problem doesn't support {language} yet.", "permanent")


def _error_response(message: str, error_type: str) -> dict:
    return {
        "status": "compile_error",
        "compile_error": message,
        "error_type": error_type,
        "visible_tests": [], "hidden_tests": [], "passed": 0, "total": 0,
    }
