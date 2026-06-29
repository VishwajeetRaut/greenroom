"""
Run once (or any time you want to reset Supabase's `questions` table back to
the bundled seed) to push backend/data/question_bank.json into Supabase:

    cd backend && .venv/Scripts/python scripts/seed_questions.py

After this, the question bank lives in Supabase and can be edited there
directly (add/edit/remove rows) without touching code or redeploying.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from services.supabase_client import get_supabase  # noqa: E402


def main():
    sb = get_supabase()
    if not sb:
        print("Supabase is not configured (check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).")
        return

    seed_path = os.path.join(os.path.dirname(__file__), "..", "data", "question_bank.json")
    with open(seed_path, "r", encoding="utf-8") as f:
        questions = json.load(f)

    for q in questions:
        row = {
            "id": q["id"],
            "track": q["track"],
            "topic": q.get("topic"),
            "difficulty": q.get("difficulty"),
            "title": q["title"],
            "prompt": q["prompt"],
            "function_name": q["function_name"],
            "languages": q["languages"],
            "tests": q["tests"],
            "constraints": q.get("constraints"),
            "examples": q.get("examples"),
        }
        sb.table("questions").upsert(row).execute()
        print(f"  upserted {q['id']}")

    print(f"Done — {len(questions)} questions seeded into Supabase.")


if __name__ == "__main__":
    main()
