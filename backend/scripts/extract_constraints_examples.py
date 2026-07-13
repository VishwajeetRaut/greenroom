"""
Extracts structured "constraints" and "examples" fields from the existing
`prompt` text of LeetCode-style question-bank entries — these aren't being
invented: the original LeetCode problem text (already present in the
LeetCodeDataset-derived prompts) already states its constraints and example
input/output in prose, this just pulls that into separate fields the
frontend can render as distinct panels instead of leaving it all as one
prose blob.

Low-risk by construction: this is extraction/reformatting of data already
verified to belong to the problem (the prompt itself), not new claims about
problem behavior — unlike the test-case/harness generation work, there's no
sandboxed verification step here because there's nothing computational to
verify. A spot-check against a handful of known problems is enough.

Usage:
    cd backend && .venv/Scripts/python scripts/extract_constraints_examples.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from services.llm import _fallback_chat, _make_llm  # noqa: E402

_BANK_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "question_bank.json")

_SYSTEM = """\
You extract structured data from a coding-interview problem's prompt text. The prompt already \
states its own constraints and at least one example input/output — you are reformatting what's \
already there, not inventing new constraints or examples.

Reply with ONLY a JSON object, no markdown fences, no explanation, in this exact shape:
{
  "constraints": ["<constraint 1, e.g. '2 <= nums.length <= 10^4'>", ...],
  "examples": [
    {"input": "<example input as stated or reasonably restated, e.g. 'nums = [2,7,11,15], target = 9'>",
     "output": "<example output, e.g. '[0,1]'>",
     "explanation": "<short reason if the prompt gives one, else empty string>"}
  ]
}

If the prompt states no explicit constraints, return an empty list for "constraints" — do not \
invent plausible-sounding ones. If the prompt gives no explicit example, derive ONE example from \
the test cases provided below (these are already verified correct) rather than leaving examples \
empty."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _extract(question: dict) -> dict | None:
    sample_tests = question["tests"][:2]
    tests_preview = "\n".join(f'  {t["call"]} -> {t["expected"]}' for t in sample_tests)
    user = f"Prompt:\n{question['prompt']}\n\nA couple of its verified test cases (for reference only):\n{tests_preview}"

    try:
        llm = _make_llm(temperature=0.1, max_tokens=800)
        result = llm.invoke([SystemMessage(content=_SYSTEM), HumanMessage(content=user)])
        raw = _strip_fences(str(result.content))
    except Exception:
        try:
            raw = _strip_fences(_fallback_chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                max_tokens=800, temperature=0.1,
            ))
        except Exception as exc:
            print(f"    error: {exc}")
            return None

    try:
        data = json.loads(raw)
        if "constraints" in data and "examples" in data:
            return data
    except json.JSONDecodeError as exc:
        print(f"    parse error: {exc}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    with open(_BANK_PATH, "r", encoding="utf-8") as f:
        bank = json.load(f)

    candidates = [q for q in bank if not q["id"].startswith("cc-") and "constraints" not in q]
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Processing {len(candidates)} problems...")
    done = 0
    for q in candidates:
        data = _extract(q)
        if data:
            q["constraints"] = data["constraints"]
            q["examples"] = data["examples"]
            done += 1
            print(f"  OK: {q['id']} ({len(data['constraints'])} constraints, {len(data['examples'])} examples)")
        else:
            print(f"  SKIP: {q['id']}")

    with open(_BANK_PATH, "w", encoding="utf-8") as f:
        json.dump(bank, f, indent=2)

    print(f"\nDone: {done}/{len(candidates)} extracted. Saved to {_BANK_PATH}")


if __name__ == "__main__":
    main()
