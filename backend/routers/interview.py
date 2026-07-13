import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from auth import AuthenticatedUser, get_current_user
from models import (
    BoilerplateResponse,
    CodeJobStatusResponse,
    DiagramEvaluation,
    EndSessionRequest,
    EndSessionResponse,
    MessageRequest,
    MessageResponse,
    QuestionContext,
    RunCodeJobResponse,
    RunCodeRequest,
    RunTestsRequest,
    RunTestsResponse,
    StartSessionRequest,
    StartSessionResponse,
)
from services import (
    harness_generator,
    job_store,
    llm,
    piston,
    question_bank,
    question_generator,
    test_runner,
)
from services.persistence import (
    persist_assigned_question,
    persist_evaluation,
    persist_message,
    persist_session_start,
)
from services.rate_limit import check_rate_limit
from services.session_guard import check_idle_timeout, check_ownership, check_session_limit
from services.session_store import SESSIONS, evict, get_session, now, session_lock
from services.supabase_client import get_supabase

router = APIRouter(prefix="/interview", tags=["interview"])


def _question_context(assigned: dict) -> QuestionContext:
    is_stdio = bool(assigned.get("tests") and "stdin" in assigned["tests"][0])
    return QuestionContext(
        id=assigned["id"],
        title=assigned.get("title", ""),
        difficulty=assigned.get("difficulty", ""),
        constraints=assigned.get("constraints") or [],
        examples=assigned.get("examples") or [],
        is_stdio=is_stdio,
    )


@router.post("/start", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)
    check_session_limit(user.id)

    session_id = str(uuid.uuid4())
    greeting = await run_in_threadpool(llm.opening_message, req.track, req.role)

    SESSIONS[session_id] = {
        "track": req.track,
        "role": req.role,
        "history": [{"role": "interviewer", "content": greeting}],
        "user_id": user.id,
        "assigned_question": None,
        "next_sequence_no": 1,
        "last_activity_at": now(),
        "job_description": req.job_description or None,
    }

    await run_in_threadpool(
        persist_session_start, session_id, user.id, req.track, req.role, greeting,
        assigned_question_id=None,
    )

    return StartSessionResponse(session_id=session_id, track=req.track, question=greeting)


@router.post("/message", response_model=MessageResponse)
async def post_message(req: MessageRequest, user: AuthenticatedUser = Depends(get_current_user)):
    check_rate_limit(user.id)

    async with session_lock(req.session_id):
        session = get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        check_ownership(session, user)
        check_idle_timeout(session)

        candidate_content = req.message
        if req.code:
            candidate_content += f"\n\n[Candidate's current code]\n{req.code}"

        is_first_reply = (
            session["track"] in ("technical", "system-design", "behavioral")
            and session.get("assigned_question") is None
        )
        session["history"].append({"role": "candidate", "content": candidate_content})
        await run_in_threadpool(persist_message, req.session_id, "candidate", req.message, session["next_sequence_no"])
        session["next_sequence_no"] += 1

        if is_first_reply:
            if session["track"] == "technical":
                session["assigned_question"] = await question_generator.select_or_generate_question(
                    session["role"], candidate_intro=req.message,
                )
            elif session["track"] == "system-design":
                session["assigned_question"] = await run_in_threadpool(
                    question_bank.pick_system_design_question
                )
            else:
                session["assigned_question"] = await run_in_threadpool(
                    question_bank.pick_behavioral_question
                )
            if session["assigned_question"]:
                await run_in_threadpool(persist_assigned_question, req.session_id, session["assigned_question"]["id"])

        question = await run_in_threadpool(
            llm.next_question, session["track"], session["role"], session["history"],
            session.get("assigned_question"), session.get("job_description"),
        )

        session["history"].append({"role": "interviewer", "content": question})
        await run_in_threadpool(persist_message, req.session_id, "interviewer", question, session["next_sequence_no"])
        session["next_sequence_no"] += 1
        session["last_activity_at"] = now()

    ctx = _question_context(session["assigned_question"]) if is_first_reply and session.get("assigned_question") else None
    return MessageResponse(question=question, question_context=ctx)


@router.post("/code/run", response_model=RunCodeJobResponse)
async def run_code(
    req: RunCodeRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Enqueues code execution and returns a job_id immediately.
    The client polls GET /code/job/{job_id} for the result."""
    check_rate_limit(user.id, max_per_minute=20)
    jid = job_store.create_job()

    async def _execute():
        try:
            result = await piston.run_code(req.language, req.version, req.source, req.stdin or "")
            job_store.set_result(jid, result)
        except Exception as exc:
            job_store.set_error(jid, str(exc))

    background_tasks.add_task(_execute)
    return RunCodeJobResponse(job_id=jid)


@router.get("/code/job/{job_id}", response_model=CodeJobStatusResponse)
async def get_code_job(job_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    """Poll for the result of an async code execution job."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return CodeJobStatusResponse(status=job["status"], result=job.get("result"))


@router.get("/{session_id}/boilerplate", response_model=BoilerplateResponse)
async def get_boilerplate(session_id: str, language: str, user: AuthenticatedUser = Depends(get_current_user)):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    check_ownership(session, user)

    assigned = session.get("assigned_question")
    if not assigned:
        return BoilerplateResponse(boilerplate=None, supported=True)

    is_stdio = bool(assigned.get("tests") and "stdin" in assigned["tests"][0])
    bank_lang = "cpp" if language == "gcc" else language
    allowed = set(assigned.get("languages") or [])
    if "cpp" in allowed:
        allowed.add("gcc")

    if is_stdio:
        return BoilerplateResponse(boilerplate=None, supported=True)

    if bank_lang in (assigned.get("languages") or []):
        if bank_lang in ("python", "node"):
            signature = await harness_generator.get_or_generate_signature(assigned, bank_lang)
            return BoilerplateResponse(boilerplate=signature, supported=True)
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
    check_ownership(session, user)

    assigned = session.get("assigned_question")
    is_stdio = bool(assigned and assigned.get("tests") and "stdin" in assigned["tests"][0])
    if is_stdio:
        assert assigned is not None  # is_stdio is only True when assigned is truthy
        parsed = await test_runner.run_stdio_tests(
            req.language, req.version, req.source,
            assigned["tests"], assigned.get("visible_count", 3),
        )
        return RunTestsResponse(**parsed)

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
            compile_error=f"Couldn't auto-generate a verified {req.language} harness for this problem. Switch to Python or JavaScript, or try again — harness generation uses the LLM and occasionally fails on first attempt.",
            error_type="transient",
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
            visible_tests=[], hidden_tests=[], passed=0, total=0,
        )

    result = await piston.run_code(req.language, req.version, harness)
    raw = result.get("run", {})
    parsed = test_runner.parse_results(raw.get("stdout", ""), raw.get("stderr", ""))
    return RunTestsResponse(**parsed)


@router.delete("/{session_id}")
def delete_session(session_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    session = get_session(session_id)
    if session:
        check_ownership(session, user)

    evict(session_id)

    sb = get_supabase()
    if sb:
        sb.table("evaluations").delete().eq("session_id", session_id).execute()
        sb.table("messages").delete().eq("session_id", session_id).execute()
        sb.table("sessions").delete().eq("id", session_id).execute()
    return {"deleted": session_id}


@router.post("/end", response_model=EndSessionResponse)
async def end_session(req: EndSessionRequest, user: AuthenticatedUser = Depends(get_current_user)):
    async with session_lock(req.session_id):
        session = get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        check_ownership(session, user)

        has_candidate_answer = any(t["role"] == "candidate" for t in session["history"])
        if not has_candidate_answer:
            empty_summary = "No answers were recorded in this session. Start a new session and answer at least one question to receive a score."
            empty_result = {
                "overall_score": 0,
                "summary": empty_summary,
                "evaluations": [],
            }
            await run_in_threadpool(persist_evaluation, req.session_id, empty_result)
            return EndSessionResponse(overall_score=0, summary=empty_summary, evaluations=[])

        result = await run_in_threadpool(llm.evaluate_session, session["track"], session["role"], session["history"])

        # For system-design sessions: score the candidate's diagram separately
        diagram_eval = None
        assigned = session.get("assigned_question")
        if session["track"] == "system-design" and assigned and assigned.get("expected_components"):
            diagram_eval = await run_in_threadpool(llm.evaluate_diagram, session["history"], assigned)
            result["diagram_evaluation"] = diagram_eval

        await run_in_threadpool(persist_evaluation, req.session_id, result)

    return EndSessionResponse(
        overall_score=result.get("overall_score", 5),
        summary=result.get("summary", ""),
        star_analysis=result.get("star_analysis"),
        evaluations=result.get("evaluations", []),
        diagram_evaluation=DiagramEvaluation(**diagram_eval) if diagram_eval else None,
    )
