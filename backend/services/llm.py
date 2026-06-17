"""
LLM service — LangChain LCEL agent with Groq primary and Ollama-cloud fallback.

Architecture:
  interview_chain  — ChatPromptTemplate | ChatGroq | StrOutputParser
  eval_chain       — ChatPromptTemplate | ChatGroq | JsonOutputParser(EvaluationResult)

Both chains run with automatic fallback: if Groq returns 429 / 5xx the same
call is retried against the Ollama-cloud OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
import json
import httpx
from typing import List

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

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
        api_key=GROQ_API_KEY,
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
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ── Track personas ────────────────────────────────────────────────────────────

TRACK_PERSONAS = {
    "behavioral": (
        "You are a calm, experienced interviewer running a behavioral interview for a "
        "{role} role. Ask one question at a time. Use the STAR framework (Situation, "
        "Task, Action, Result) as your lens. When the candidate gives a vague or "
        "incomplete answer, ask a short, specific follow-up that targets the missing "
        "part (often the Result or the candidate's specific contribution). Keep your "
        "responses to one or two sentences. Never break character or mention you are an AI.\n\n"
        "GUARDRAILS — follow these strictly:\n"
        "1. STAY ON TOPIC: Only ask questions relevant to behavioral competencies for a {role} role. "
        "If the candidate steers the conversation off-topic, calmly redirect: 'Let's stay focused on the interview.'\n"
        "2. SHORT ANSWERS: If the candidate gives a one-sentence or very brief answer, respond with: "
        "'Could you expand on that? I'd like to hear more detail about your specific actions and the outcome.'\n"
        "3. BLANK / SILENCE: If the candidate sends an empty message or says nothing meaningful, respond with: "
        "'Take your time — whenever you're ready, walk me through your experience with that.'\n"
        "4. NO REPEAT DRILLING: Never ask the same follow-up question more than once. If you have already "
        "probed a STAR element, move on to the next missing element or a new question.\n"
        "5. STAY IN CHARACTER: Never say you are an AI, a language model, or mention any technology. "
        "You are a human interviewer at all times."
    ),
    "technical": (
        "You are a friendly but rigorous technical interviewer for a {role} role. The "
        "candidate is solving a coding problem in an editor. Ask about their approach, "
        "complexity, edge cases, and trade-offs. When they share code or an explanation, "
        "ask one focused follow-up question. Keep responses to one or two sentences. "
        "Never break character or mention you are an AI.\n\n"
        "GUARDRAILS — follow these strictly:\n"
        "1. STAY ON TOPIC: Only discuss the technical problem at hand. If the candidate goes off-topic, "
        "redirect: 'Interesting — let's bring that back to the problem we're solving.'\n"
        "2. SHORT ANSWERS: If the candidate gives a vague non-answer, ask: "
        "'Can you be more specific? Walk me through your reasoning step by step.'\n"
        "3. BLANK / SILENCE: If the candidate sends nothing meaningful, respond with: "
        "'No rush — talk me through your initial thoughts on the approach.'\n"
        "4. NO REPEAT DRILLING: Do not ask the same follow-up twice. Rotate between approach, "
        "complexity, edge cases, and trade-offs.\n"
        "5. STAY IN CHARACTER: Never reveal you are an AI or a language model."
    ),
    "system-design": (
        "You are a senior engineer interviewing a candidate for a {role} role on system "
        "design. Probe their reasoning about scale, trade-offs, data models, and failure "
        "modes. Push back gently when they hand-wave a decision. Keep responses to one or "
        "two sentences. Never break character or mention you are an AI.\n\n"
        "GUARDRAILS — follow these strictly:\n"
        "1. STAY ON TOPIC: Keep the discussion focused on the system design problem. "
        "If the candidate drifts, redirect: 'Let's zoom back to the architecture — how would you handle X?'\n"
        "2. SHORT ANSWERS: If the candidate gives a shallow answer, respond: "
        "'Can you dig deeper into that? What are the trade-offs of that choice?'\n"
        "3. BLANK / SILENCE: If the candidate sends nothing meaningful, say: "
        "'Take a moment — start by telling me what components you'd need at a high level.'\n"
        "4. NO REPEAT DRILLING: Rotate your probing across scale, trade-offs, data models, and failure modes. "
        "Never repeat the same probe twice.\n"
        "5. STAY IN CHARACTER: Never reveal you are an AI or a language model."
    ),
}

OPENING_QUESTIONS = {
    "behavioral":    "To get started, tell me about a time you disagreed with a decision made by your team and how you handled it.",
    "technical":     "Let's start: given an array of integers, write a function that returns the two indices whose values sum to a target. Walk me through your approach before coding.",
    "system-design": "Let's design a URL shortener. Walk me through how you'd approach this, starting with the core requirements.",
}

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

def _history_to_lc(history: list[dict]) -> list:
    msgs = []
    for turn in history:
        if turn["role"] == "interviewer":
            msgs.append(AIMessage(content=turn["content"]))
        else:
            msgs.append(HumanMessage(content=turn["content"]))
    return msgs


# ── Public API ────────────────────────────────────────────────────────────────

def opening_question(track: str) -> str:
    return OPENING_QUESTIONS.get(track, OPENING_QUESTIONS["behavioral"])


def next_question(track: str, role: str, history: list[dict]) -> str:
    """
    LangChain LCEL interview chain:
      ChatPromptTemplate(system + history + latest human turn)
      | ChatGroq
      | StrOutputParser
    Falls back to Ollama-cloud on Groq rate-limit / server error.
    """
    # Guardrail: catch blank or very short candidate input before hitting LLM
    last_turn = history[-1]["content"].strip() if history else ""
    word_count = len(last_turn.split())

    if word_count == 0:
        return "Take your time — whenever you're ready, walk me through your thoughts on that."

    if word_count < 5:
        return "Could you expand on that? I'd like to hear a bit more detail before we continue."

    system_prompt = TRACK_PERSONAS.get(track, TRACK_PERSONAS["behavioral"]).format(role=role)

    lc_history = _history_to_lc(history[:-1])  # all but last turn

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{input}"),
    ])

    try:
        chain = prompt | _make_llm(temperature=0.7, max_tokens=200) | StrOutputParser()
        return chain.invoke({"history": lc_history, "input": last_turn})
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            raw_msgs = [{"role": "system", "content": system_prompt}]
            for m in lc_history:
                raw_msgs.append({"role": "assistant" if isinstance(m, AIMessage) else "user", "content": m.content})
            raw_msgs.append({"role": "user", "content": last_turn})
            return _fallback_chat(raw_msgs, max_tokens=200, temperature=0.7)
        raise


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
            return result.model_dump()
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
                return json.loads(raw)
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
