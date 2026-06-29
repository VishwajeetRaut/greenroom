"""
One-off curation tool — NOT part of the running app, never invoked from
request handling. Run locally to (re)build backend/data/question_bank.json
from the open-source LeetCodeDataset (newfacade, MIT license, arXiv:2504.14655
— see THIRD_PARTY_NOTICES.md), with full correctness verification.

Every imported test case is checked by actually running the dataset's own
canonical solution against it inside this script (exec() of trusted,
locally-downloaded dataset content only) — if the assertion doesn't hold,
the problem is dropped rather than imported. This is the same bar applied
to the hand-written seed questions (see verify_question_bank.py): no test
case enters our bank unless it has been proven correct.

Usage:
    cd backend && .venv/Scripts/python scripts/import_leetcode_dataset.py [--limit N]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re


def _slugify_topic(tags: list[str]) -> str:
    if not tags:
        return "general"
    return re.sub(r"[^a-z0-9]+", "-", tags[0].lower()).strip("-")


def _parse_test_cases(test_src: str, entry_point: str) -> list[dict] | None:
    """`test_src` is a `def check(candidate): assert candidate(...) == ...` block.
    Returns [{"call": "...", "expected": "..."}] with `candidate` replaced by the
    real entry point, or None if the assertions can't be parsed cleanly."""
    try:
        tree = ast.parse(test_src)
    except SyntaxError:
        return None

    func = next((n for n in tree.body if isinstance(n, ast.FunctionDef)), None)
    if func is None:
        return None

    cases = []
    for node in func.body:
        if not isinstance(node, ast.Assert):
            continue
        cmp = node.test
        if not (isinstance(cmp, ast.Compare) and len(cmp.ops) == 1 and isinstance(cmp.ops[0], ast.Eq)):
            continue
        call_node = cmp.left
        expected_node = cmp.comparators[0]
        if not (isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Name) and call_node.func.id == "candidate"):
            continue
        call_node.func = ast.Name(id="__ENTRY__", ctx=ast.Load())
        call_src = ast.unparse(call_node).replace("__ENTRY__", entry_point)
        expected_src = ast.unparse(expected_node)
        cases.append({"call": call_src, "expected": expected_src})

    return cases or None


def _verify(prompt: str, completion: str, entry_point: str, cases: list[dict]) -> bool:
    """Runs the dataset's own canonical solution against every parsed case.
    Returns True only if ALL cases pass."""
    ns: dict = {}
    try:
        exec(prompt + "\n" + completion, ns)
    except Exception:
        return False

    for tc in cases:
        try:
            actual = eval(tc["call"], ns)
            expected = eval(tc["expected"], ns)
            if actual != expected:
                return False
        except Exception:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Max number of verified problems to keep")
    ap.add_argument(
        "--input", default=os.path.join(os.path.dirname(__file__), "..", "data", "leetcode_dataset_test.jsonl")
    )
    ap.add_argument(
        "--output", default=os.path.join(os.path.dirname(__file__), "..", "data", "leetcode_imported.json")
    )
    args = ap.parse_args()

    imported = []
    seen_ids = set()
    total = 0
    rejected_parse = 0
    rejected_verify = 0

    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            total += 1
            rec = json.loads(line)

            entry_point = rec["entry_point"]  # e.g. "Solution().twoSum"
            cases = _parse_test_cases(rec["test"], entry_point)
            if not cases:
                rejected_parse += 1
                continue

            if not _verify(rec["prompt"], rec["completion"], entry_point, cases):
                rejected_verify += 1
                continue

            qid = rec["task_id"]
            if qid in seen_ids:
                continue
            seen_ids.add(qid)

            imported.append({
                "id": qid,
                "track": "technical",
                "topic": _slugify_topic(rec.get("tags") or []),
                "difficulty": (rec.get("difficulty") or "medium").lower(),
                "title": qid.replace("-", " ").title(),
                "prompt": rec["problem_description"].strip(),
                "function_name": entry_point,
                "languages": ["python"],
                "tests": cases[:8],  # cap per-problem test count to keep payloads small
            })

            if args.limit and len(imported) >= args.limit:
                break

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(imported, f, indent=2)

    print(f"Processed {total} records.")
    print(f"  rejected (couldn't parse assertions): {rejected_parse}")
    print(f"  rejected (canonical solution failed verification): {rejected_verify}")
    print(f"  imported & verified: {len(imported)}")
    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
