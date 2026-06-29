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
        "The coding problem for this session is: **{problem_title}** (Difficulty: {problem_difficulty}). "
        "Topics: {problem_topics}. "
        "After the candidate has introduced themselves, present this problem in full — "
        "write out the complete description, examples with input/output, and constraints exactly "
        "as they appear on LeetCode. Then follow up on their approach, time/space complexity, "
        "edge cases, and trade-offs one question at a time. "
        "The candidate has a live code editor open. "
        "Keep follow-up responses to one or two sentences. Never break character or mention you are an AI."
    ),
    "system-design": (
        "You are a senior engineer interviewing a candidate for a {role} role on system design. "
        "The conversation so far may include a brief greeting and the candidate's introduction "
        "— once they have introduced themselves, naturally present a system design problem "
        "suited to their background, then probe their reasoning about scale, trade-offs, data "
        "models, and failure modes. Push back gently when they hand-wave a decision. "
        "Keep responses to one or two sentences. Never break character or mention you are an AI."
    ),
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

def opening_message(track: str, role: str) -> str:
    """LLM-generated warm greeting that opens the interview session."""
    system = OPENING_SYSTEM_PROMPT.format(track=track, role=role)
    try:
        llm = _make_llm(temperature=0.9, max_tokens=120)
        result = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content="[The interview session is starting now.]"),
        ])
        return result.content.strip()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            return _fallback_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": "[The interview session is starting now.]"},
                ],
                max_tokens=120, temperature=0.9,
            )
        raise


def next_question(track: str, role: str, history: list[dict], problem: dict | None = None) -> str:
    """
    LangChain LCEL interview chain:
      ChatPromptTemplate(system + history + latest human turn)
      | ChatGroq
      | StrOutputParser
    Falls back to Ollama-cloud on Groq rate-limit / server error.
    """
    persona = TRACK_PERSONAS.get(track, TRACK_PERSONAS["behavioral"])
    if track == "technical" and problem:
        system_prompt = persona.format(
            role=role,
            problem_title=problem.get("title", "a coding problem"),
            problem_difficulty=problem.get("difficulty", "Medium"),
            problem_topics=", ".join(problem.get("topics", [])[:4]),
        )
    else:
        system_prompt = persona.format(role=role) if track != "technical" else persona.format(
            role=role, problem_title="a coding problem", problem_difficulty="Medium", problem_topics="Data Structures"
        )

    # Split history: everything except the last candidate turn goes into
    # MessagesPlaceholder; the last candidate turn is the current "human" input.
    lc_history = _history_to_lc(history[:-1])  # all but last turn
    last_turn = history[-1]["content"] if history else ""

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
            # Build OpenAI-format messages for fallback
            raw_msgs = [{"role": "system", "content": system_prompt}]
            for m in lc_history:
                raw_msgs.append({"role": "assistant" if isinstance(m, AIMessage) else "user", "content": m.content})
            raw_msgs.append({"role": "user", "content": last_turn})
            return _fallback_chat(raw_msgs, max_tokens=200, temperature=0.7)
        raise


_PROBLEM_FORMAT_SYSTEM = """\
You extract DSA coding problems from interviewer messages.

If the message introduces a NEW coding problem, return a JSON object with this exact shape:
{
  "title": "Short problem title",
  "difficulty": "Easy",
  "description": "Full problem description",
  "examples": [
    {"input": "nums = [2,7,11,15], target = 9", "output": "[0,1]", "explanation": "nums[0] + nums[1] == 9"}
  ],
  "constraints": ["2 <= nums.length <= 10^4", "-10^9 <= nums[i] <= 10^9"],
  "boilerplate": {
    "python": "def two_sum(nums, target):\\n    pass",
    "javascript": "function twoSum(nums, target) {\\n    \\n}",
    "java": "class Solution {\\n    public int[] twoSum(int[] nums, int target) {\\n        \\n    }\\n}",
    "cpp": "#include <bits/stdc++.h>\\nusing namespace std;\\n\\nclass Solution {\\npublic:\\n    vector<int> twoSum(vector<int>& nums, int target) {\\n        \\n    }\\n};"
  }
}

If the message is a follow-up question, hint, feedback, or does NOT introduce a new coding problem, return exactly: null

Reply ONLY with the JSON object or the word null. No explanation, no markdown fences."""


def format_problem_statement(question: str) -> dict | None:
    """Parse a technical interviewer message into a structured LeetCode-style problem. Returns None if not a coding problem."""
    prompt = f"Interviewer message:\n{question}"
    msgs = [
        {"role": "system", "content": _PROBLEM_FORMAT_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    try:
        llm = _make_llm(temperature=0.1, max_tokens=800)
        from langchain_core.messages import SystemMessage, HumanMessage
        result = llm.invoke([SystemMessage(content=_PROBLEM_FORMAT_SYSTEM), HumanMessage(content=prompt)])
        raw = _strip_fences_json(result.content.strip())
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            raw = _strip_fences_json(_fallback_chat(msgs, max_tokens=800, temperature=0.1))
        else:
            return None

    if raw.lower() == "null" or not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "title" in data:
            return data
    except json.JSONDecodeError:
        pass
    return None


def _strip_fences_json(text: str) -> str:
    import re
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


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
