"""
Step 1 of 2 for importing CodeContests (DeepMind, CC-BY-4.0, see
THIRD_PARTY_NOTICES.md) — extracts stdin/stdout test cases from the parquet
file and writes a TO-VERIFY intermediate file containing each problem's
reference C++ solution (used only for verification, never shipped to
candidates — see verify_codecontests_docker.py, which must run in an
isolated container since it executes this untrusted reference code).

Usage:
    cd backend && .venv/Scripts/python scripts/import_codecontests.py
"""
from __future__ import annotations

import json
import os
import re

import pyarrow.parquet as pq

_DIFFICULTY_MAP = {
    0: "medium", 1: "easy", 2: "medium", 3: "hard", 4: "hard", 5: "hard", 6: "medium",
    7: "easy", 8: "easy", 9: "easy",            # A, B, C
    10: "medium", 11: "medium", 12: "medium",   # D, E, F
}


def _difficulty(d: int) -> str:
    return _DIFFICULTY_MAP.get(d, "hard")


def _slugify(name: str) -> str:
    name = re.sub(r"^\d+_?[A-Z]?\.?\s*", "", name)  # strip "1575_A. " contest prefix
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "problem"


def main():
    path = os.path.join(os.path.dirname(__file__), "..", "data", "code_contests_test.parquet")
    table = pq.read_table(path)
    rows = table.to_pylist()

    to_verify = []
    skipped_no_cpp = 0
    skipped_no_tests = 0

    for row in rows:
        sol = row.get("solutions") or {}
        languages = sol.get("language") or []
        solutions = sol.get("solution") or []
        cpp_solution = next((s for lang, s in zip(languages, solutions) if lang == 2), None)
        if not cpp_solution:
            skipped_no_cpp += 1
            continue

        public = row.get("public_tests") or {}
        private = row.get("private_tests") or {}
        visible = list(zip(public.get("input") or [], public.get("output") or []))[:3]
        hidden = list(zip(private.get("input") or [], private.get("output") or []))[:5]
        if len(visible) < 1 or len(visible) + len(hidden) < 3:
            skipped_no_tests += 1
            continue

        slug = _slugify(row["name"])
        to_verify.append({
            "id": f"cc-{slug}",
            "title": row["name"],
            "prompt": row["description"].strip(),
            "topic": (row.get("cf_tags") or ["general"])[0],
            "difficulty": _difficulty(row.get("difficulty") or 0),
            "tests": [{"stdin": i, "stdout": o} for i, o in (visible + hidden)],
            "visible_count": len(visible),
            "_cpp_solution": cpp_solution,  # stripped before final output, never shipped
        })

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "codecontests_to_verify.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(to_verify, f, indent=2)

    print(f"Total rows: {len(rows)}")
    print(f"  skipped (no C++ reference solution): {skipped_no_cpp}")
    print(f"  skipped (not enough test cases): {skipped_no_tests}")
    print(f"  candidates to verify: {len(to_verify)}")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
