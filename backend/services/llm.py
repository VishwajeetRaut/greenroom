"""
LLM service — LangChain LCEL agent with Groq primary and Ollama-cloud fallback.

Architecture:
  interview_chain  — ChatPromptTemplate | ChatGroq | StrOutputParser
  eval_chain       — ChatPromptTemplate | ChatGroq | JsonOutputParser(EvaluationResult)

Both chains run with automatic fallback: if Groq returns 429 / 5xx the same
call is retried against the Ollama-cloud OpenAI-compatible endpoint.
"""

from __future__ import annotations

import json
import os
import re
from typing import List

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from services import guardrail, llm_cache, token_meter
from services.logger import log

# ── env ──────────────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL     = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

FALLBACK_BASE_URL = os.environ.get("FALLBACK_BASE_URL", "")
FALLBACK_API_KEY  = os.environ.get("FALLBACK_API_KEY", "")
FALLBACK_MODEL    = os.environ.get("FALLBACK_MODEL", "llama3.3:70b")

EVAL_SELF_CRITIQUE_ENABLED = os.environ.get("EVAL_SELF_CRITIQUE_ENABLED", "true").lower() == "true"

# Azure OpenAI — used only for the end-of-session evaluation report (the
# score/feedback the candidate sees after finishing): evaluate_session,
# _self_critique, evaluate_diagram. Everything else (opening greeting, the
# live interview conversation, question selection, guardrail checks, harness
# generation) stays on Groq — untouched.
AZURE_OPENAI_API_KEY    = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT   = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# How many distinct opening greetings to keep per (track, role) before serving
# them from cache instead of generating a new one. See opening_message.
OPENING_POOL_SIZE = int(os.environ.get("LLM_OPENING_POOL_SIZE", "5"))


# ── Pydantic schemas for structured evaluation output ────────────────────────

class STARAnalysis(BaseModel):
    situation: str = Field(description="Assessment of how well the candidate described the situation")
    task:      str = Field(description="Assessment of how well the candidate described their role/task")
    action:    str = Field(description="Assessment of how well the candidate described their actions")
    result:    str = Field(description="Assessment of how well the candidate described the outcome")
    star_score: int = Field(ge=0, le=10, description="Overall STAR framework completeness score 0-10")
    missing_elements: List[str] = Field(description="STAR elements the candidate skipped or left vague")

class CategoryScore(BaseModel):
    category: str
    score: int = Field(ge=0, le=10)
    feedback: str

class EvaluationResult(BaseModel):
    overall_score: int = Field(ge=0, le=10)
    summary: str = Field(description="2-3 sentence summary with the single most useful improvement")
    star_analysis: STARAnalysis
    evaluations: List[CategoryScore]


# ── LangChain LLM (with Groq) ────────────────────────────────────────────────

def _make_llm(
    temperature: float = 0.7, max_tokens: int = 300,
    call_site: str = "unattributed", model: str | None = None,
) -> ChatGroq:
    """Single construction point for every Groq call.

    call_site is what makes token accounting useful — the interesting question
    isn't total spend but which part of a session is expensive, so usage is
    tagged here rather than aggregated anonymously. The recorder is attached
    as a callback (not read off the return value) because several call sites
    are LCEL chains ending in an output parser, which discards the AIMessage
    carrying the usage metadata before the caller ever sees it.

    model overrides GROQ_MODEL for a single call — used by
    scripts/benchmark_models.py to run the same workload across models.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set.")
    resolved = model or GROQ_MODEL
    return ChatGroq(
        api_key=GROQ_API_KEY,
        model=resolved,
        temperature=temperature,
        max_tokens=max_tokens,
        callbacks=[token_meter.UsageRecorder(call_site, provider="groq", model=resolved)],
    )


def _make_azure_llm(
    temperature: float = 0.7, max_tokens: int = 300,
    call_site: str = "unattributed",
) -> AzureChatOpenAI:
    """Evaluation-only LLM (Azure OpenAI gpt-5-mini). Used by evaluate_session,
    _self_critique, and evaluate_diagram — nowhere else.

    gpt-5-mini is a reasoning-family model: it only accepts the default
    `temperature` (1) — passing any other value 400s — and without
    `reasoning_effort` pinned down, hidden reasoning tokens consume the
    `max_completion_tokens` budget before any visible output is written
    (observed: a 700-token budget spent entirely on reasoning, zero output,
    request errors out). `reasoning_effort="minimal"` disables that hidden
    pass so the existing token budgets (tuned for Groq's plain chat model)
    keep working unchanged. `temperature` is accepted for call-site
    compatibility with `_make_llm` but not forwarded."""
    if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError("Azure OpenAI is not configured (AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT missing).")
    return AzureChatOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        azure_deployment=AZURE_OPENAI_DEPLOYMENT,
        api_version=AZURE_OPENAI_API_VERSION,
        max_completion_tokens=max_tokens,
        reasoning_effort="minimal",
        # Same recorder as _make_llm. Evaluation moved to Azure after this
        # metering was written, and _make_azure_llm is a separate constructor —
        # so without this line the whole evaluation path (evaluate_session,
        # _self_critique, evaluate_diagram) records nothing, which is exactly
        # the blind spot this module exists to remove. gpt-5-mini has no entry
        # in token_meter.PRICING, so it reports exact token counts and shows up
        # under `unpriced_models` rather than silently costing $0.
        callbacks=[token_meter.UsageRecorder(
            call_site, provider="azure", model=AZURE_OPENAI_DEPLOYMENT,
        )],
    )


# ── Fallback: Ollama-cloud (OpenAI-compatible REST) ──────────────────────────

def _fallback_chat(
    messages: list[dict], max_tokens: int, temperature: float, json_mode: bool = False,
    call_site: str = "unattributed",
) -> str:
    """This path never touches LangChain, so the callback in _make_llm can't
    see it — usage is recorded directly off the OpenAI-compatible response."""
    if not FALLBACK_BASE_URL or not FALLBACK_API_KEY:
        raise RuntimeError("Fallback LLM not configured (FALLBACK_BASE_URL / FALLBACK_API_KEY missing).")
    payload: dict = {
        "model": FALLBACK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = httpx.post(
        f"{FALLBACK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {FALLBACK_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.json()
    token_meter.record_openai_usage(call_site, "fallback", body)
    return body["choices"][0]["message"]["content"].strip()


# ── Track personas ────────────────────────────────────────────────────────────

OPENING_SYSTEM_PROMPT = (
    "You are a warm, professional interviewer opening a {track} interview for a {role} role. "
    "This is the very first message of the session. Greet the candidate naturally, make them "
    "feel at ease, and ask them to walk you through their background and experience. "
    "Keep it to 2-3 sentences. Never mention you are an AI."
)

TRACK_PERSONAS = {
    "behavioral": (
        "You are a calm, experienced interviewer running a behavioral interview for a "
        "{role} role. The conversation so far may include a brief greeting and the candidate's "
        "introduction — once they have introduced themselves, move naturally into your first "
        "behavioral question, then continue one question at a time. "
        "Use the STAR framework (Situation, Task, Action, Result) as your lens. When the "
        "candidate gives a vague or incomplete answer, ask a short, specific follow-up that "
        "targets the missing part. Keep responses to one or two sentences. "
        "Never break character or mention you are an AI."
    ),
    "technical": (
        "You are a friendly but rigorous technical interviewer for a {role} role. "
        "The conversation so far may include a brief greeting and the candidate's introduction "
        "— once they have introduced themselves, naturally transition into a coding problem "
        "relevant to their background, then follow up on their approach, complexity, edge cases, "
        "and trade-offs one question at a time. The candidate has a live code editor open. "
        "Keep responses to one or two sentences. Never break character or mention you are an AI. "
        "CRITICAL: never state the time or space complexity of any solution (no Big-O, no "
        "'runs in linear/constant time', etc.) — always ask the candidate to derive and justify "
        "it themselves. If you'd normally say 'that's O(n)', instead ask 'what's the time "
        "complexity of that, and why?' "
        "CRITICAL: the candidate is assigned exactly ONE coding problem for the entire session — "
        "the code editor cannot be swapped to a new problem, so once a problem has been given, "
        "NEVER introduce a second one or say anything like 'let's move on to another problem/"
        "challenge'. Once the coding problem is assigned, every remaining turn must be a follow-up "
        "on THAT SAME problem — its approach, complexity, edge cases, trade-offs, alternative "
        "implementations, or how it'd change under different constraints. If you've run out of "
        "follow-ups on the coding problem, transition into behavioral/experience questions instead "
        "of a new coding problem."
    ),
    "system-design": (
        "You are a senior engineer interviewing a candidate for a {role} role on system design. "
        "The conversation so far may include a brief greeting and the candidate's introduction "
        "— once they have introduced themselves, naturally present a system design problem "
        "suited to their background, then probe their reasoning about scale, trade-offs, data "
        "models, and failure modes. Push back gently when they hand-wave a decision. "
        "Keep responses to one or two sentences. Never break character or mention you are an AI. "
        "CRITICAL: never reveal or recommend a specific architectural decision (which database, "
        "caching strategy, queueing system, or scaling pattern to use) — always ask the candidate "
        "to propose and defend their own choice instead of suggesting one yourself. "
        "CRITICAL: the candidate is assigned exactly ONE system design problem for the entire "
        "session, and their diagram is graded against THAT problem's expected components — "
        "NEVER introduce a second design problem or say anything like 'let's design something "
        "else/a different system'. Once the problem is presented, every remaining turn must probe "
        "THAT SAME design (scale, trade-offs, data models, failure modes, or how it changes under "
        "different constraints)."
    ),
}

DIAGRAM_EVAL_PROMPT = """\
You are a senior staff engineer reviewing a system design interview.

The candidate was solving this problem: {title}

Expected key components for a good design:
{expected_components}

Architecture diagrams the candidate drew during the session (serialized from their board):
{diagram_descriptions}

Evaluate the candidate's diagram:
1. Which expected components did they include? (list by name, lowercase)
2. Which expected components are missing or absent?
3. Proximity score 0-10: 0 = no diagram / completely wrong, 5 = core present but gaps, 10 = thorough and well-connected.
4. Label: "needs work" (0-3), "reasonable" (4-6), "strong" (7-10).
5. One sentence of the most important actionable feedback.

Reply ONLY as valid JSON, no markdown fences:
{{
  "components_found": ["<string>", ...],
  "components_missing": ["<string>", ...],
  "proximity_score": <int 0-10>,
  "proximity_label": "needs work" | "reasonable" | "strong",
  "feedback": "<string>"
}}"""

EVAL_SYSTEM_PROMPT = """\
You are an expert interview coach analysing a mock {track} interview for a {role} role.

Your job:
1. Score the candidate on clarity, structure, and confidence (1-10 each). For technical/system-design tracks also score "technical depth".
2. Perform a STAR-framework analysis (behavioral tracks) or solution-quality analysis (technical/system-design). Score STAR completeness 0-10 and list any missing elements.
3. Write a 2-3 sentence overall summary. End with the single most actionable improvement.

Reply ONLY as valid JSON matching this exact schema — no markdown fences, no extra keys:
{{
  "overall_score": <int 0-10>,
  "summary": "<string>",
  "star_analysis": {{
    "situation": "<string>",
    "task": "<string>",
    "action": "<string>",
    "result": "<string>",
    "star_score": <int 0-10>,
    "missing_elements": ["<string>", ...]
  }},
  "evaluations": [
    {{"category": "<string>", "score": <int 0-10>, "feedback": "<string>"}},
    ...
  ]
}}"""


CRITIQUE_SYSTEM_PROMPT = """\
You are a senior interview coach reviewing a JUNIOR coach's evaluation of a mock {track} \
interview for a {role} role, checking it against the transcript before it goes to the candidate.

Look for:
- Scores that don't match the written feedback (e.g. a 8/10 score paired with feedback describing \
a weak answer, or vice versa).
- Feedback that is generic filler rather than grounded in something the candidate actually said.
- Missed evidence in the transcript that should have moved a score up or down.
- STAR/solution-quality analysis that contradicts the transcript.

If the draft evaluation is already accurate and well-grounded, return it UNCHANGED. Only edit scores \
or text where you find a genuine mismatch — do not make cosmetic changes for their own sake.

Reply ONLY as valid JSON matching this exact schema — no markdown fences, no extra keys, no commentary:
{{
  "overall_score": <int 0-10>,
  "summary": "<string>",
  "star_analysis": {{
    "situation": "<string>",
    "task": "<string>",
    "action": "<string>",
    "result": "<string>",
    "star_score": <int 0-10>,
    "missing_elements": ["<string>", ...]
  }},
  "evaluations": [
    {{"category": "<string>", "score": <int 0-10>, "feedback": "<string>"}},
    ...
  ]
}}"""


# ── Helper: convert internal history to LangChain messages ───────────────────

def _history_to_lc(history: list[dict]) -> list:
    msgs = []
    for turn in history:
        if turn["role"] == "interviewer":
            msgs.append(AIMessage(content=turn["content"]))
        else:
            msgs.append(HumanMessage(content=turn["content"]))
    return msgs


# ── Public API ────────────────────────────────────────────────────────────────

def opening_message(track: str, role: str) -> str:
    """LLM-generated warm greeting that opens the interview session.

    Cached as a variant POOL rather than a single response: the inputs
    (track, role) take only a handful of distinct values across the whole
    product, so without caching every session start pays for a greeting that
    is near-identical to one already generated. Collapsing it to one cached
    string would make every candidate hear the exact same sentence forever,
    so the first OPENING_POOL_SIZE sessions per (track, role) generate and
    fill the pool, and every session after that is served free from it.
    """
    return llm_cache.pooled_call(
        "llm.opening",
        (track, role, GROQ_MODEL),
        lambda: _opening_message_uncached(track, role),
        pool_size=OPENING_POOL_SIZE,
        prompt_chars=len(OPENING_SYSTEM_PROMPT.format(track=track, role=role)),
    )


def _opening_message_uncached(track: str, role: str) -> str:
    import time
    system = OPENING_SYSTEM_PROMPT.format(track=track, role=role)
    start = time.monotonic()
    try:
        llm_client = _make_llm(temperature=0.9, max_tokens=120, call_site="opening_message")
        result = llm_client.invoke([
            SystemMessage(content=system),
            HumanMessage(content="[The interview session is starting now.]"),
        ])
        log.info("llm.opening", track=track, latency_ms=round((time.monotonic() - start) * 1000), provider="groq")
        return result.content.strip()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            log.warning("llm.opening.fallback", track=track, error=str(exc))
            result = _fallback_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": "[The interview session is starting now.]"},
                ],
                max_tokens=120, temperature=0.9, call_site="opening_message",
            )
            log.info("llm.opening", track=track, latency_ms=round((time.monotonic() - start) * 1000), provider="fallback")
            return result
        raise


def next_question(
    track: str, role: str, history: list[dict], assigned_question: dict | None = None,
    job_description: str | None = None, is_new_assignment: bool = False,
) -> str:
    """
    LangChain LCEL interview chain:
      ChatPromptTemplate(system + history + latest human turn)
      | ChatGroq
      | StrOutputParser
    Falls back to Ollama-cloud on Groq rate-limit / server error.

    Output passes through the guardrail layer (services.guardrail) before it
    reaches the candidate — see that module for why this exists.

    is_new_assignment: True only on the turn `assigned_question` is first being
    presented. On every later turn, the platform can't actually swap the code
    editor to a different problem, so the guardrail also blocks the model from
    trying to introduce a second one — prompt-hardening alone isn't reliable
    enough (see TRACK_PERSONAS["technical"]), so this is defense in depth.

    assigned_question: for "technical" sessions, a problem pulled from the
    curated question bank (services.question_bank) — when present, the
    interviewer presents this exact problem instead of inventing one, so the
    later test-runner can grade against verified canonical test cases.
    """
    system_prompt = TRACK_PERSONAS.get(track, TRACK_PERSONAS["behavioral"]).format(role=role)
    if job_description:
        system_prompt += f"\n\n[Job description the candidate is interviewing for]\n{job_description.strip()}"
    if track == "behavioral" and assigned_question:
        expected = assigned_question.get("expected_elements") or []
        elements_note = (
            f" Listen for: {', '.join(expected)}." if expected else ""
        )
        system_prompt += (
            f"\n\nFocus this behavioral session on the following question: "
            f"\"{assigned_question['prompt']}\"\n\n"
            f"Present this question naturally once the candidate has introduced themselves, "
            f"then ask targeted follow-up questions to surface the Situation, Task, Action, "
            f"and Result in their answer.{elements_note}"
        )
    if track == "system-design" and assigned_question:
        system_prompt += (
            f"\n\nThe system design problem for this session is: {assigned_question['prompt']}\n\n"
            "Keep probing the candidate's design choices, component selection, trade-offs, "
            "and how they would handle scale and failure."
        )
    if track == "technical" and assigned_question:
        is_stdio = bool(assigned_question.get("tests") and "stdin" in assigned_question["tests"][0])
        if is_stdio:
            io_note = (
                "The candidate must write a complete program that reads input from stdin and "
                "prints the answer to stdout — not just a function — since this problem is graded "
                "by running their program against raw input/output, the same way Codeforces does."
            )
        else:
            from services.question_bank import parse_function_name
            class_name, method_name = parse_function_name(assigned_question.get("function_name"))
            if class_name:
                io_note = (
                    f"The candidate should implement it as a method named `{method_name}` inside "
                    f"a class called `{class_name}`."
                )
            else:
                io_note = (
                    f"The candidate should implement it as a function named "
                    f"`{method_name or 'the appropriate signature'}`."
                )
        system_prompt += (
            f"\n\nThe coding problem assigned to this candidate is exactly this one — present it "
            f"(you may paraphrase the wording, but keep the requirements identical) once their "
            f"introduction is done, then follow up on their approach: {assigned_question['prompt']}\n\n{io_note}"
        )

    # Split history: everything except the last candidate turn goes into
    # MessagesPlaceholder; the last candidate turn is the current "human" input.
    lc_history = _history_to_lc(history[:-1])  # all but last turn
    last_turn = history[-1]["content"] if history else ""

    def _ask(temperature: float = 0.7, corrective: str | None = None) -> str:
        sys_prompt = system_prompt
        if corrective:
            sys_prompt += f"\n\nIMPORTANT: {corrective}"
        try:
            p = ChatPromptTemplate.from_messages([
                ("system", sys_prompt),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ])
            chain = p | _make_llm(temperature=temperature, max_tokens=200, call_site="next_question") | StrOutputParser()
            return chain.invoke({"history": lc_history, "input": last_turn})
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None or status == 429 or (isinstance(status, int) and status >= 500):
                raw_msgs = [{"role": "system", "content": sys_prompt}]
                for m in lc_history:
                    raw_msgs.append({"role": "assistant" if isinstance(m, AIMessage) else "user", "content": m.content})
                raw_msgs.append({"role": "user", "content": last_turn})
                return _fallback_chat(raw_msgs, max_tokens=200, temperature=temperature, call_site="next_question")
            raise

    import time as _time
    _start = _time.monotonic()
    draft = _ask()
    result = guardrail.sanitize(
        draft, track,
        regenerate_fn=lambda: _ask(temperature=0.4, corrective=(
            "your previous draft leaked information the candidate must figure out themselves "
            "(an exact complexity or a specific architectural recommendation). Rewrite it so it "
            "ONLY asks a question — never states the answer."
        )),
    )

    if track in ("technical", "system-design") and assigned_question and not is_new_assignment:
        what = "coding problem" if track == "technical" else "system design problem"
        locked_ui = "the code editor cannot switch to a different one" if track == "technical" \
            else "their diagram is graded against this one problem's expected components"
        result = guardrail.sanitize_no_new_problem(
            result,
            regenerate_fn=lambda: _ask(temperature=0.4, corrective=(
                f"your previous draft tried to introduce a NEW {what}. The candidate has "
                f"exactly ONE assigned problem for this entire session and {locked_ui} — "
                f"rewrite your response so it follows up on the ALREADY ASSIGNED problem instead: "
                f"{assigned_question['prompt']}"
            )),
        )

    log.info("llm.next_question", track=track, latency_ms=round((_time.monotonic() - _start) * 1000))
    return result


def _reconcile_score(result: dict) -> None:
    """Replace the LLM's self-reported overall_score with the mean of the
    per-category scores so the number always matches the written critique."""
    scores = [e["score"] for e in result.get("evaluations", []) if isinstance(e.get("score"), (int, float))]
    if scores:
        result["overall_score"] = round(sum(scores) / len(scores))


def _self_critique(track: str, role: str, transcript: str, draft: dict) -> dict:
    """Second LLM pass: have a reviewer persona check the draft evaluation
    against the transcript and correct any score/feedback mismatches.
    Best-effort — any failure just returns the original draft unchanged."""
    if not EVAL_SELF_CRITIQUE_ENABLED:
        return draft
    try:
        system_content = CRITIQUE_SYSTEM_PROMPT.format(track=track, role=role)
        human_content = (
            f"Transcript:\n{transcript or 'The candidate did not answer any questions.'}\n\n"
            f"Draft evaluation to review:\n{json.dumps(draft)}"
        )
        llm = _make_azure_llm(temperature=0.2, max_tokens=700, call_site="evaluate_session.self_critique")
        llm_json = llm.bind(response_format={"type": "json_object"})
        parser = JsonOutputParser(pydantic_object=EvaluationResult)
        chain = llm_json | parser
        reviewed = chain.invoke([
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ])
        if hasattr(reviewed, "model_dump"):
            reviewed = reviewed.model_dump()
        _reconcile_score(reviewed)
        reviewed = _validate_eval_result(reviewed)
        log.info("llm.evaluate_session.self_critique", track=track,
                  score_changed=reviewed.get("overall_score") != draft.get("overall_score"))
        return reviewed
    except Exception as exc:
        log.warning("llm.evaluate_session.self_critique_failed", track=track, error=str(exc))
        return draft


def _validate_eval_result(result: dict) -> dict:
    """JsonOutputParser only uses the pydantic_object for format instructions,
    not enforcement — malformed LLM JSON (e.g. missing overall_score) passes
    through untouched otherwise. Re-validate explicitly so a bad response is
    treated as a failure (triggers fallback/retry) instead of silently
    persisting a null score."""
    validated = EvaluationResult(**result)
    return validated.model_dump()


def evaluate_session(track: str, role: str, history: list[dict]) -> dict:
    """
    LangChain LCEL evaluation chain:
      ChatPromptTemplate(system + transcript)
      | ChatGroq (json_mode)
      | JsonOutputParser(EvaluationResult)
    Falls back to Ollama-cloud on Groq rate-limit / server error.
    """
    transcript = "\n".join(
        f"{'Interviewer' if t['role'] == 'interviewer' else 'Candidate'}: {t['content']}"
        for t in history
    )

    system_content = EVAL_SYSTEM_PROMPT.format(track=track, role=role)

    # Build messages directly — the eval prompt contains literal JSON braces
    # which LangChain's template parser would misinterpret as variables.
    lc_messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=transcript or "The candidate did not answer any questions."),
    ]

    parser = JsonOutputParser(pydantic_object=EvaluationResult)

    try:
        llm = _make_azure_llm(temperature=0.3, max_tokens=700, call_site="evaluate_session")
        llm_json = llm.bind(response_format={"type": "json_object"})
        chain = llm_json | parser
        result = chain.invoke(lc_messages)
        # Pydantic model → plain dict for the rest of the app
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        _reconcile_score(result)
        result = _validate_eval_result(result)
        return _self_critique(track, role, transcript, result)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            raw = _fallback_chat(
                [
                    {"role": "system", "content": system_content},
                    {"role": "user",   "content": transcript or "The candidate did not answer any questions."},
                ],
                max_tokens=700, temperature=0.3, json_mode=True, call_site="evaluate_session",
            )
            try:
                # Some fallback providers wrap JSON in markdown fences even
                # with response_format=json_object set — strip before parsing.
                cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip())
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                result = json.loads(cleaned)
                _reconcile_score(result)
                result = _validate_eval_result(result)
                return _self_critique(track, role, transcript, result)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        # Last-resort default
        return {
            "overall_score": 5,
            "summary": "Could not generate a detailed report this time. Your transcript has been saved.",
            "star_analysis": {
                "situation": "—", "task": "—", "action": "—", "result": "—",
                "star_score": 0, "missing_elements": [],
            },
            "evaluations": [],
        }


def _extract_diagram_descriptions(history: list[dict]) -> str:
    """Pull [Architecture diagram] blocks from candidate messages."""
    import re as _re
    diagrams = []
    for turn in history:
        if turn["role"] != "candidate":
            continue
        for block in _re.findall(r"\[Architecture diagram\].*?(?=\n\n[A-Z]|\Z)", turn["content"], _re.DOTALL):
            diagrams.append(block.strip())
    if not diagrams:
        return "No architecture diagram was drawn during this session."
    return "\n\n---\n\n".join(diagrams)


_SHAPE_TYPES = {"rectangle", "ellipse", "diamond", "triangle", "trapezoid", "parallelogram"}


def _describe_diagram_elements(elements: list[dict]) -> str | None:
    """Python port of the frontend's generateBoardDescription() (see
    useInterviewSession.js) — builds the same [Architecture diagram] text
    from raw Excalidraw scene elements, so the autosaved board (persisted via
    POST /api/interview/diagram) can be scored even if its final state was
    never echoed into a chat message. Mirrors the frontend logic exactly so
    both paths produce identical, LLM-comparable descriptions."""
    if not elements:
        return None
    label_of: dict[str, str] = {}
    for el in elements:
        if el.get("type") == "text" and el.get("containerId"):
            cid = el["containerId"]
            label_of[cid] = (label_of.get(cid, "") + (el.get("text") or "").strip())
    for el in elements:
        if el.get("type") == "text" and not el.get("containerId") and (el.get("text") or "").strip():
            label_of[el["id"]] = el["text"].strip()

    components = [
        label_of.get(el["id"], el["type"]) for el in elements if el.get("type") in _SHAPE_TYPES
    ]
    connections = []
    for el in elements:
        if el.get("type") != "arrow":
            continue
        start = (el.get("startBinding") or {}).get("elementId")
        end = (el.get("endBinding") or {}).get("elementId")
        if not start or not end:
            continue
        from_label = label_of.get(start, "node")
        to_label = label_of.get(end, "node")
        via = label_of.get(el["id"])
        connections.append(f"{from_label} --[{via}]--> {to_label}" if via else f"{from_label} → {to_label}")

    if not components and not connections:
        return None
    parts = ["[Architecture diagram]"]
    if components:
        parts.append(f"Components: {', '.join(components)}")
    if connections:
        parts.append(f"Connections: {', '.join(connections)}")
    return "\n".join(parts)


def evaluate_diagram(
    history: list[dict], assigned_question: dict, diagram_elements: list[dict] | None = None,
) -> dict:
    """
    LLM call that scores the candidate's system-design diagram against the
    expected_components list on the assigned question.
    Returns a dict matching the DiagramEvaluation model.

    diagram_elements: the raw, autosaved board state (session["diagram_elements"]
    — see POST /api/interview/diagram), preferred over parsing chat history
    since a candidate who draws their diagram and immediately ends the session
    never gets a chance to echo it into a message (see generateBoardDescription
    in useInterviewSession.js, called only from handleSend). Falls back to
    the message-embedded description for older sessions/paths.
    """
    expected = assigned_question.get("expected_components") or []
    diagrams = _describe_diagram_elements(diagram_elements) or _extract_diagram_descriptions(history)
    prompt = DIAGRAM_EVAL_PROMPT.format(
        title=assigned_question.get("title", "the assigned problem"),
        expected_components=", ".join(expected) if expected else "(not specified)",
        diagram_descriptions=diagrams,
    )

    _default = {
        "components_found": [],
        "components_missing": expected,
        "proximity_score": 0,
        "proximity_label": "needs work",
        "feedback": "No architecture diagram was submitted — draw your design on the board and send it with your answer.",
    }

    try:
        llm_client = _make_azure_llm(temperature=0.1, max_tokens=400, call_site="evaluate_diagram")
        llm_json = llm_client.bind(response_format={"type": "json_object"})
        chain = llm_json | JsonOutputParser()
        result = chain.invoke([HumanMessage(content=prompt)])
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return result
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            try:
                raw = _fallback_chat(
                    [{"role": "user", "content": prompt}],
                    max_tokens=400, temperature=0.1, json_mode=True, call_site="evaluate_diagram",
                )
                cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip())
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                return json.loads(cleaned)
            except Exception:
                pass
        return _default
