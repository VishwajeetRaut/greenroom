"""
Building an evaluation transcript that the model can actually accept.

The problem
-----------
Every candidate turn appends the candidate's *entire current code* to the
history:

    candidate_content += f"\\n\\n[Candidate's current code]\\n{req.code}"

`code` is capped at 100,000 characters and `message` at 20,000, and there are
up to 15 candidate turns — so a transcript can reach ~1.8M characters. The
same file is re-sent on every turn, so most of that is duplicated copies of
earlier revisions of one program. System-design sessions do the same thing
with the serialised `[Architecture diagram]` block.

Measured, not theorised: a 128,000-character transcript (15 turns × a ~8KB
file) was billed by Groq at **55,467 tokens** — 55% of the free tier's entire
100,000-token daily allowance, in a single call. It failed with a 429, and
because the fallback path in `evaluate_session` was unguarded, the exception
escaped and `POST /interview/end` returned a 500: the candidate finished a
two-hour interview and got nothing.

Note the token ratio. Code tokenises at roughly **2.3 characters per token**,
not the ~4 that prose does, so a chars/4 estimate understates a code-heavy
transcript by about 70%. `estimate_tokens` is deliberately pessimistic for
that reason — under-estimating here means a failed evaluation, while
over-estimating just means compacting slightly sooner than strictly needed.

The approach
------------
Three stages, applied only as far as needed, so a normal session is completely
unaffected:

1. Under budget → send the transcript verbatim. Identical to previous
   behaviour for every session that already worked.
2. Over budget → drop superseded code and diagram blocks, keeping the final
   version of each in full. Nearly all the signal, a fraction of the size.
3. Still over → the caller chunks it and evaluates map-reduce style
   (`services.llm._evaluate_chunked`).
"""

from __future__ import annotations

import os
import re

# Pessimistic on purpose — see the module docstring. Code-heavy transcripts
# measured at ~2.3 chars/token against Groq's own accounting.
_CHARS_PER_TOKEN = 2.3

# Budget for the transcript alone, excluding the system prompt and the
# response. Sized by the daily token quota rather than by the context window:
# llama-3.3-70b accepts 128k tokens, but spending 55k of a 100k daily
# allowance on one evaluation is a failure even when it technically fits.
MAX_TRANSCRIPT_TOKENS = int(os.environ.get("EVAL_MAX_TRANSCRIPT_TOKENS", "12000"))

_CODE_MARKER = "[Candidate's current code]"
_DIAGRAM_MARKER = "[Architecture diagram]"

# A block runs from its marker to a blank line followed by a capital letter
# (the next prose paragraph), or to the end of the message — the same shape
# services.llm._extract_diagram_descriptions already relies on.
_BLOCK_RE = re.compile(
    rf"({re.escape(_CODE_MARKER)}|{re.escape(_DIAGRAM_MARKER)})\n(.*?)(?=\n\n[A-Z]|\Z)",
    re.DOTALL,
)


def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def render(history: list[dict]) -> str:
    """The transcript exactly as evaluate_session has always built it."""
    return "\n".join(
        f"{'Interviewer' if turn['role'] == 'interviewer' else 'Candidate'}: {turn['content']}"
        for turn in history
    )


def _blocks_by_marker(history: list[dict]) -> dict[str, int]:
    """Index of the last turn containing each marker, so everything before it
    can be treated as superseded."""
    last_seen: dict[str, int] = {}
    for index, turn in enumerate(history):
        for marker in (_CODE_MARKER, _DIAGRAM_MARKER):
            if marker in (turn.get("content") or ""):
                last_seen[marker] = index
    return last_seen


def compact(history: list[dict]) -> list[dict]:
    """Replace superseded code/diagram blocks with a one-line placeholder,
    keeping the most recent version of each in full.

    The candidate re-sends their whole file every turn, so earlier copies are
    revisions of the same program — the final state plus the conversation
    around it carries essentially all the evaluable signal. The placeholder is
    left in place rather than deleted so the evaluator can still see that the
    candidate was iterating, and roughly how much they wrote.
    """
    last_seen = _blocks_by_marker(history)
    if not last_seen:
        return history

    compacted: list[dict] = []
    for index, turn in enumerate(history):
        content = turn.get("content") or ""

        def replace(match: re.Match) -> str:
            marker, body = match.group(1), match.group(2)
            if last_seen.get(marker) == index:
                return match.group(0)  # the current version — keep it in full
            label = "code" if marker == _CODE_MARKER else "diagram"
            return f"{marker}\n(an earlier {label} revision, {len(body)} characters, superseded below)"

        compacted.append({**turn, "content": _BLOCK_RE.sub(replace, content)})
    return compacted


def build(history: list[dict], max_tokens: int | None = None) -> tuple[str, bool]:
    """Returns (transcript, fits) for a single-call evaluation.

    `fits` is False when even the compacted transcript is over budget — the
    caller should chunk instead of sending it and hoping.
    """
    budget = max_tokens or MAX_TRANSCRIPT_TOKENS

    full = render(history)
    if estimate_tokens(full) <= budget:
        return full, True

    compacted = render(compact(history))
    return compacted, estimate_tokens(compacted) <= budget


def chunks(history: list[dict], max_tokens: int | None = None) -> list[str]:
    """Split a compacted history into transcript slices that each fit the
    budget, breaking only on turn boundaries so no answer is cut in half.

    A single turn that exceeds the budget on its own (a candidate pasting a
    100,000-character file in one message) is hard-truncated — it is the only
    case where content is genuinely lost, and losing the tail of one oversized
    paste is much better than failing the whole evaluation.
    """
    budget = max_tokens or MAX_TRANSCRIPT_TOKENS
    budget_chars = int(budget * _CHARS_PER_TOKEN)

    out: list[str] = []
    current: list[str] = []
    current_chars = 0

    for turn in compact(history):
        speaker = "Interviewer" if turn["role"] == "interviewer" else "Candidate"
        line = f"{speaker}: {turn['content']}"

        if len(line) > budget_chars:
            if current:
                out.append("\n".join(current))
                current, current_chars = [], 0
            out.append(line[:budget_chars] + "\n…(this turn was truncated — it exceeded one chunk on its own)")
            continue

        if current_chars + len(line) > budget_chars and current:
            out.append("\n".join(current))
            current, current_chars = [], 0

        current.append(line)
        current_chars += len(line) + 1

    if current:
        out.append("\n".join(current))
    return out
