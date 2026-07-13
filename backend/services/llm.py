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
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field, SecretStr

from services import guardrail
from services.logger import log

# ── env ──────────────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL     = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

FALLBACK_BASE_URL = os.environ.get("FALLBACK_BASE_URL", "")
FALLBACK_API_KEY  = os.environ.get("FALLBACK_API_KEY", "")
FALLBACK_MODEL    = os.environ.get("FALLBACK_MODEL", "llama3.3:70b")


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

def _make_llm(temperature: float = 0.7, max_tokens: int = 300) -> ChatGroq:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set.")
    return ChatGroq(
        api_key=SecretStr(GROQ_API_KEY),
        model=GROQ_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ── Fallback: Ollama-cloud (OpenAI-compatible REST) ──────────────────────────

def _fallback_chat(messages: list[dict], max_tokens: int, temperature: float, json_mode: bool = False) -> str:
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
    return resp.json()["choices"][0]["message"]["content"].strip()


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
        "complexity of that, and why?'"
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
        "to propose and defend their own choice instead of suggesting one yourself."
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


# ── Helper: convert internal history to LangChain messages ───────────────────

def _history_to_lc(history: list[dict]) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for turn in history:
        if turn["role"] == "interviewer":
            msgs.append(AIMessage(content=turn["content"]))
        else:
            msgs.append(HumanMessage(content=turn["content"]))
    return msgs


# ── Public API ────────────────────────────────────────────────────────────────

def opening_message(track: str, role: str) -> str:
    """LLM-generated warm greeting that opens the interview session."""
    import time
    system = OPENING_SYSTEM_PROMPT.format(track=track, role=role)
    start = time.monotonic()
    try:
        llm_client = _make_llm(temperature=0.9, max_tokens=120)
        response = llm_client.invoke([
            SystemMessage(content=system),
            HumanMessage(content="[The interview session is starting now.]"),
        ])
        log.info("llm.opening", track=track, latency_ms=round((time.monotonic() - start) * 1000), provider="groq")
        return str(response.content).strip()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            log.warning("llm.opening.fallback", track=track, error=str(exc))
            fallback_text = _fallback_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": "[The interview session is starting now.]"},
                ],
                max_tokens=120, temperature=0.9,
            )
            log.info("llm.opening", track=track, latency_ms=round((time.monotonic() - start) * 1000), provider="fallback")
            return fallback_text
        raise


def next_question(track: str, role: str, history: list[dict], assigned_question: dict | None = None, job_description: str | None = None) -> str:
    """
    LangChain LCEL interview chain:
      ChatPromptTemplate(system + history + latest human turn)
      | ChatGroq
      | StrOutputParser
    Falls back to Ollama-cloud on Groq rate-limit / server error.

    Output passes through the guardrail layer (services.guardrail) before it
    reaches the candidate — see that module for why this exists.

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

    def _ask(temperature: float = 0.7, corrective: bool = False) -> str:
        sys_prompt = system_prompt
        if corrective:
            sys_prompt += (
                "\n\nIMPORTANT: your previous draft leaked information the candidate must "
                "figure out themselves (an exact complexity or a specific architectural "
                "recommendation). Rewrite it so it ONLY asks a question — never states the answer."
            )
        try:
            p = ChatPromptTemplate.from_messages([
                ("system", sys_prompt),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ])
            chain = p | _make_llm(temperature=temperature, max_tokens=200) | StrOutputParser()
            return chain.invoke({"history": lc_history, "input": last_turn})
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status is None or status == 429 or (isinstance(status, int) and status >= 500):
                raw_msgs = [{"role": "system", "content": sys_prompt}]
                for m in lc_history:
                    raw_msgs.append({"role": "assistant" if isinstance(m, AIMessage) else "user", "content": str(m.content)})
                raw_msgs.append({"role": "user", "content": last_turn})
                return _fallback_chat(raw_msgs, max_tokens=200, temperature=temperature)
            raise

    import time as _time
    _start = _time.monotonic()
    draft = _ask()
    result = guardrail.sanitize(draft, track, regenerate_fn=lambda: _ask(temperature=0.4, corrective=True))
    log.info("llm.next_question", track=track, latency_ms=round((_time.monotonic() - _start) * 1000))
    return result


def _reconcile_score(result: dict) -> None:
    """Replace the LLM's self-reported overall_score with the mean of the
    per-category scores so the number always matches the written critique."""
    scores = [e["score"] for e in result.get("evaluations", []) if isinstance(e.get("score"), (int, float))]
    if scores:
        result["overall_score"] = round(sum(scores) / len(scores))


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
        llm = _make_llm(temperature=0.3, max_tokens=700)
        llm_json = llm.bind(response_format={"type": "json_object"})
        chain = llm_json | parser
        result = chain.invoke(lc_messages)
        # Pydantic model → plain dict for the rest of the app
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        _reconcile_score(result)
        return result
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            raw = _fallback_chat(
                [
                    {"role": "system", "content": system_content},
                    {"role": "user",   "content": transcript or "The candidate did not answer any questions."},
                ],
                max_tokens=700, temperature=0.3, json_mode=True,
            )
            try:
                # Some fallback providers wrap JSON in markdown fences even
                # with response_format=json_object set — strip before parsing.
                cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip())
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                result = json.loads(cleaned)
                _reconcile_score(result)
                return result
            except json.JSONDecodeError:
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


def evaluate_diagram(history: list[dict], assigned_question: dict) -> dict:
    """
    LLM call that scores the candidate's system-design diagram against the
    expected_components list on the assigned question.
    Returns a dict matching the DiagramEvaluation model.
    """
    expected = assigned_question.get("expected_components") or []
    diagrams = _extract_diagram_descriptions(history)
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
        llm_client = _make_llm(temperature=0.1, max_tokens=400)
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
                    max_tokens=400, temperature=0.1, json_mode=True,
                )
                cleaned = re.sub(r"^```[a-z]*\n?", "", raw.strip())
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
                return json.loads(cleaned)
            except Exception:
                pass
        return _default
